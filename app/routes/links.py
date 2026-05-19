import ipaddress
import re
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlencode, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, text
from sqlmodel import Session, select

from ..auth import get_current_user
from ..database import engine as db_engine, get_session
from ..models import Group, Link, LinkGroupLink, LinkTagLink, Tag, User
from ..ratelimit import rate_limit
from ..templates_cfg import templates
from ..utils import descendant_group_ids, get_or_create_tag, refresh_link_fts, sidebar_data

router = APIRouter()

PER_PAGE = 30
MAX_TAGS_PER_LINK = 50
MAX_TITLE_LEN = 500
MAX_DESC_LEN = 2000
MAX_NOTE_LEN = 50_000

_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_host(host: str) -> bool:
    if not host:
        return True
    if host.lower() in ("localhost", "local", "broadcasthost", "0.0.0.0"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return any(ip in net for net in _PRIVATE_NETS)
    except ValueError:
        return False  # hostname DNS valide - autorisé


def _fts_escape(q: str) -> str:
    words = re.findall(r"\w+", q)
    if not words:
        return ""
    return " ".join(f"{w}*" for w in words)


def _safe_url(url: str) -> bool:
    if not url or url.startswith("//"):
        return False
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return False
        return not _is_private_host(p.hostname or "")
    except Exception:
        return False


async def _fetch_meta(url: str) -> dict:
    if not _safe_url(url):
        return {"title": "", "description": "", "favicon_url": ""}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10, max_redirects=5) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Excerpta/1.0)"})
        if resp.status_code >= 400:
            return {"title": "", "description": "", "favicon_url": ""}
        final_url = str(resp.url)
        if not _safe_url(final_url):
            return {"title": "", "description": "", "favicon_url": ""}
        parsed = urlparse(final_url)
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.find("title")
        desc = soup.find("meta", attrs={"property": "og:description"}) or soup.find(
            "meta", attrs={"name": "description"}
        )
        description_text = desc.get("content", "").strip() if desc else ""
        if not description_text:
            for p in soup.find_all("p"):
                text = p.get_text(" ", strip=True)
                if len(text) > 80:
                    description_text = text[:400]
                    break
        icon = soup.find("link", rel=lambda r: r and "icon" in r)
        favicon = ""
        if icon and icon.get("href"):
            href = icon["href"]
            if href.startswith("//"):
                href = f"{parsed.scheme}:{href}"
            elif not href.startswith("http"):
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            if _safe_url(href):  # reject protocol-relative & private IPs in favicon
                favicon = href
        if not favicon:
            candidate = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
            if _safe_url(candidate):
                favicon = candidate
        og_img = soup.find("meta", attrs={"property": "og:image"}) or \
                 soup.find("meta", attrs={"name": "twitter:image"})
        thumbnail = ""
        if og_img:
            raw = og_img.get("content", "").strip()
            if raw.startswith("//"):
                raw = f"{parsed.scheme}:{raw}"
            elif raw.startswith("/"):
                raw = f"{parsed.scheme}://{parsed.netloc}{raw}"
            if _safe_url(raw):
                thumbnail = raw
        if not thumbnail:
            content_zone = soup.find(["article", "main"]) or soup
            for img in content_zone.find_all("img"):
                raw = (
                    img.get("src", "")
                    or img.get("data-src", "")
                    or img.get("data-lazy-src", "")
                    or img.get("data-original", "")
                ).strip()
                if not raw or raw.startswith("data:"):
                    continue
                if raw.lower().endswith(".svg"):
                    continue
                try:
                    w = int(img.get("width") or 0)
                    h = int(img.get("height") or 0)
                    if (w and w < 100) or (h and h < 100):
                        continue
                except ValueError:
                    pass
                if raw.startswith("//"):
                    raw = f"{parsed.scheme}:{raw}"
                elif raw.startswith("/"):
                    raw = f"{parsed.scheme}://{parsed.netloc}{raw}"
                if raw.startswith("http") and _safe_url(raw):
                    thumbnail = raw
                    break
        return {
            "title": title.text.strip() if title else "",
            "description": description_text,
            "favicon_url": favicon,
            "thumbnail_url": thumbnail,
        }
    except Exception:
        return {"title": "", "description": "", "favicon_url": ""}


async def _fetch_and_update_meta(link_id: int, url: str) -> None:
    """Récupère les métadonnées en arrière-plan et complète les champs vides du lien."""
    meta = await _fetch_meta(url)
    with Session(db_engine) as s:
        link = s.get(Link, link_id)
        if not link:
            return
        changed = False
        if (not link.title or link.title == url) and meta.get("title"):
            link.title = meta["title"][:MAX_TITLE_LEN]
            changed = True
        if not link.description and meta.get("description"):
            link.description = meta["description"][:MAX_DESC_LEN]
            changed = True
        if not link.favicon_url and meta.get("favicon_url"):
            link.favicon_url = meta["favicon_url"]
            changed = True
        if not link.thumbnail_url and meta.get("thumbnail_url"):
            link.thumbnail_url = meta["thumbnail_url"]
            changed = True
        if changed:
            s.add(link)
            s.flush()
            tags = list(s.exec(
                select(Tag).join(LinkTagLink, LinkTagLink.tag_id == Tag.id)
                .where(LinkTagLink.link_id == link.id)
            ).all())
            refresh_link_fts(s, link, tags)
            s.commit()


def _get_or_create_tags(session: Session, user_id: int, names: List[str]) -> List[Tag]:
    return [get_or_create_tag(session, user_id, n.strip().lower()) for n in names if n.strip()]


def _validate_group_ids(session: Session, user_id: int, raw: List[str]) -> List[int]:
    ids = [int(g) for g in raw if g.isdigit()]
    if not ids:
        return []
    owned = {
        g.id for g in session.exec(
            select(Group).where(Group.user_id == user_id, Group.id.in_(ids))
        ).all()
    }
    return [gid for gid in ids if gid in owned]


# ─── List ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def list_links(
    request: Request,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    group_id: Optional[int] = None,
    page: int = 1,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    stmt = select(Link).where(Link.user_id == user.id)

    if q:
        escaped = _fts_escape(q)
        try:
            rows = session.execute(
                text("SELECT link_id FROM fts_links WHERE fts_links MATCH :q ORDER BY rank"),
                {"q": escaped},
            ).fetchall()
            link_ids = [r[0] for r in rows]
            stmt = stmt.where(Link.id.in_(link_ids)) if link_ids else stmt.where(Link.id < 0)
        except Exception:
            q_like = f"%{q}%"
            stmt = stmt.where(
                Link.title.ilike(q_like) | Link.url.ilike(q_like) | Link.description.ilike(q_like)
            )

    if tag:
        tag_obj = session.exec(
            select(Tag).where(Tag.user_id == user.id, Tag.name == tag)
        ).first()
        if tag_obj:
            stmt = stmt.join(LinkTagLink, LinkTagLink.link_id == Link.id).where(
                LinkTagLink.tag_id == tag_obj.id
            )
        else:
            stmt = select(Link).where(Link.id < 0)

    if group_id:
        all_grps = list(session.exec(select(Group).where(Group.user_id == user.id)).all())
        gids = descendant_group_ids(all_grps, group_id)
        stmt = (
            stmt.join(LinkGroupLink, LinkGroupLink.link_id == Link.id)
            .where(LinkGroupLink.group_id.in_(gids))
            .distinct()
        )

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    page = max(1, min(page, max(1, (total + PER_PAGE - 1) // PER_PAGE)))
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    links = list(session.exec(
        stmt.order_by(Link.created_at.desc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE)
    ).all())

    def _qs(p: int) -> str:
        params: dict = {}
        if q:
            params["q"] = q
        if tag:
            params["tag"] = tag
        if group_id:
            params["group_id"] = group_id
        params["page"] = p
        return urlencode(params)

    sidebar = sidebar_data(session, user.id)

    return templates.TemplateResponse(
        "links/list.html",
        {
            "request": request,
            "links": links,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "per_page": PER_PAGE,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "prev_qs": _qs(page - 1) if page > 1 else "",
            "next_qs": _qs(page + 1) if page < total_pages else "",
            **sidebar,
            "current_tag": tag,
            "current_group": group_id,
            "q": q or "",
            "user": user,
        },
    )


# ─── Add ─────────────────────────────────────────────────────────────────────

@router.get("/links/add", response_class=HTMLResponse)
async def add_form(
    request: Request,
    url: Optional[str] = None,
    title: Optional[str] = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    meta = {"title": title or "", "description": "", "favicon_url": "", "thumbnail_url": ""}
    sidebar = sidebar_data(session, user.id)
    return templates.TemplateResponse(
        "links/form.html",
        {
            "request": request,
            "link": None,
            "prefill_url": url or "",
            "meta": meta,
            **sidebar,
            "user": user,
        },
    )


@router.post("/links/add")
async def add_link(
    request: Request,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    title: str = Form("", max_length=MAX_TITLE_LEN),
    description: str = Form("", max_length=MAX_DESC_LEN),
    note: str = Form("", max_length=MAX_NOTE_LEN),
    is_public: Optional[str] = Form(default=None),
    tags: str = Form(""),
    groups: List[str] = Form(default=[]),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not _safe_url(url):
        raise HTTPException(status_code=400, detail="URL invalide")

    link = Link(
        user_id=user.id,
        url=url,
        title=title or url,
        description=description,
        favicon_url="",
        thumbnail_url="",
        note=note,
        is_public=is_public is not None,
    )
    session.add(link)
    session.flush()

    tag_names = [t.strip() for t in tags.split(",") if t.strip()][:MAX_TAGS_PER_LINK]
    link_tags = _get_or_create_tags(session, user.id, tag_names)
    for t in link_tags:
        session.add(LinkTagLink(link_id=link.id, tag_id=t.id))

    for gid in _validate_group_ids(session, user.id, groups):
        session.add(LinkGroupLink(link_id=link.id, group_id=gid))

    session.flush()
    refresh_link_fts(session, link, link_tags)
    session.commit()

    background_tasks.add_task(_fetch_and_update_meta, link.id, url)

    return RedirectResponse(url="/", status_code=303)


# ─── Edit ────────────────────────────────────────────────────────────────────

@router.get("/links/{link_id}/edit", response_class=HTMLResponse)
async def edit_form(
    request: Request,
    link_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    link = session.get(Link, link_id)
    if not link or link.user_id != user.id:
        raise HTTPException(status_code=404)
    sidebar = sidebar_data(session, user.id)
    current_tags = ", ".join(t.name for t in link.tags)
    current_groups = [g.id for g in link.groups]
    return templates.TemplateResponse(
        "links/form.html",
        {
            "request": request,
            "link": link,
            "prefill_url": "",
            "meta": {},
            **sidebar,
            "current_tags": current_tags,
            "current_groups": current_groups,
            "user": user,
        },
    )


@router.post("/links/{link_id}/edit")
async def edit_link(
    request: Request,
    link_id: int,
    url: str = Form(...),
    title: str = Form("", max_length=MAX_TITLE_LEN),
    description: str = Form("", max_length=MAX_DESC_LEN),
    note: str = Form("", max_length=MAX_NOTE_LEN),
    is_public: Optional[str] = Form(default=None),
    tags: str = Form(""),
    groups: List[str] = Form(default=[]),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    link = session.get(Link, link_id)
    if not link or link.user_id != user.id:
        raise HTTPException(status_code=404)

    if not _safe_url(url):
        raise HTTPException(status_code=400, detail="URL invalide")

    meta = await _fetch_meta(url)
    link.url = url
    link.title = title or url
    link.description = description
    link.note = note
    link.is_public = is_public is not None
    link.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    link.thumbnail_url = meta.get("thumbnail_url", "") or link.thumbnail_url
    session.add(link)
    session.flush()

    session.execute(text("DELETE FROM link_tags WHERE link_id = :id"), {"id": link_id})
    session.flush()
    tag_names = [t.strip() for t in tags.split(",") if t.strip()][:MAX_TAGS_PER_LINK]
    link_tags = _get_or_create_tags(session, user.id, tag_names)
    for t in link_tags:
        session.add(LinkTagLink(link_id=link.id, tag_id=t.id))

    session.execute(text("DELETE FROM link_groups WHERE link_id = :id"), {"id": link_id})
    session.flush()
    for gid in _validate_group_ids(session, user.id, groups):
        session.add(LinkGroupLink(link_id=link.id, group_id=gid))

    session.flush()
    refresh_link_fts(session, link, link_tags)
    session.commit()

    return RedirectResponse(url="/", status_code=303)


# ─── Delete ──────────────────────────────────────────────────────────────────

@router.post("/links/{link_id}/delete")
async def delete_link(
    link_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    link = session.get(Link, link_id)
    if not link or link.user_id != user.id:
        raise HTTPException(status_code=404)
    session.delete(link)
    session.commit()
    return RedirectResponse(url="/", status_code=303)


# ─── API metadata fetch ───────────────────────────────────────────────────────

@router.get("/api/fetch-meta", dependencies=[Depends(rate_limit(30, 60))])
async def api_fetch_meta(url: str, user: User = Depends(get_current_user)):
    return await _fetch_meta(url)
