import re
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlencode, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlmodel import Session, select

from ..auth import get_current_user
from ..database import get_session
from ..models import Group, Link, LinkGroupLink, LinkTagLink, Tag, User
from ..ratelimit import rate_limit
from ..templates_cfg import templates
from ..utils import get_or_create_tag, refresh_link_fts, sidebar_data

router = APIRouter()

PER_PAGE = 30


def _fts_escape(q: str) -> str:
    words = re.findall(r"\w+", q)
    if not words:
        return ""
    return " ".join(f"{w}*" for w in words)


def _safe_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


async def _fetch_meta(url: str) -> dict:
    if not _safe_url(url):
        return {"title": "", "description": "", "favicon_url": ""}
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 Linky/1.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.find("title")
        desc = soup.find("meta", attrs={"property": "og:description"}) or soup.find(
            "meta", attrs={"name": "description"}
        )
        icon = soup.find("link", rel=lambda r: r and "icon" in r)
        parsed = urlparse(url)
        if icon and icon.get("href"):
            href = icon["href"]
            if href.startswith("http"):
                favicon = href
            elif href.startswith("//"):
                favicon = f"{parsed.scheme}:{href}"
            else:
                favicon = f"{parsed.scheme}://{parsed.netloc}{href}"
        else:
            favicon = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
        return {
            "title": title.text.strip() if title else "",
            "description": desc.get("content", "").strip() if desc else "",
            "favicon_url": favicon,
        }
    except Exception:
        return {"title": "", "description": "", "favicon_url": ""}


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
    base_query = select(Link).where(Link.user_id == user.id)

    if q:
        escaped = _fts_escape(q)
        try:
            rows = session.execute(
                text("SELECT link_id FROM fts_links WHERE fts_links MATCH :q ORDER BY rank"),
                {"q": escaped},
            ).fetchall()
            link_ids = [r[0] for r in rows]
        except Exception:
            link_ids = []
        links = list(session.exec(base_query.where(Link.id.in_(link_ids))).all()) if link_ids else []
    else:
        links = list(session.exec(base_query.order_by(Link.created_at.desc())).all())

    if tag:
        links = [lk for lk in links if any(t.name == tag for t in lk.tags)]
    if group_id:
        links = [lk for lk in links if any(g.id == group_id for g in lk.groups)]

    total = len(links)
    page = max(1, min(page, max(1, (total + PER_PAGE - 1) // PER_PAGE)))
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    links = links[(page - 1) * PER_PAGE : page * PER_PAGE]

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
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    meta = {}
    if url:
        meta = await _fetch_meta(url)
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
    url: str = Form(...),
    title: str = Form(""),
    description: str = Form(""),
    note: str = Form(""),
    is_public: Optional[str] = Form(default=None),
    tags: str = Form(""),
    groups: List[str] = Form(default=[]),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not _safe_url(url):
        raise HTTPException(status_code=400, detail="URL invalide")

    if not title or not description:
        meta = await _fetch_meta(url)
        if not title:
            title = meta.get("title", "")
        if not description:
            description = meta.get("description", "")
        favicon_url = meta.get("favicon_url", "")
    else:
        parsed = urlparse(url)
        favicon_url = f"{parsed.scheme}://{parsed.netloc}/favicon.ico" if parsed.netloc else ""

    link = Link(
        user_id=user.id,
        url=url,
        title=title or url,
        description=description,
        favicon_url=favicon_url,
        note=note,
        is_public=is_public is not None,
    )
    session.add(link)
    session.flush()

    tag_names = [t.strip() for t in tags.split(",") if t.strip()]
    link_tags = _get_or_create_tags(session, user.id, tag_names)
    for t in link_tags:
        session.add(LinkTagLink(link_id=link.id, tag_id=t.id))

    for gid in _validate_group_ids(session, user.id, groups):
        session.add(LinkGroupLink(link_id=link.id, group_id=gid))

    session.flush()
    refresh_link_fts(session, link, link_tags)
    session.commit()

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
    title: str = Form(""),
    description: str = Form(""),
    note: str = Form(""),
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

    link.url = url
    link.title = title or url
    link.description = description
    link.note = note
    link.is_public = is_public is not None
    link.updated_at = datetime.utcnow()
    session.add(link)
    session.flush()

    session.execute(text("DELETE FROM link_tags WHERE link_id = :id"), {"id": link_id})
    session.flush()
    tag_names = [t.strip() for t in tags.split(",") if t.strip()]
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
