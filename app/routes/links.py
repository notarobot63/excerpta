import asyncio
import hashlib
import ipaddress
import re
import socket
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlencode, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, text
from sqlmodel import Session, select

from ..auth import get_current_user
from ..database import engine as db_engine, get_session
from ..models import Folder, Link, LinkTagLink, Tag, User
from ..ratelimit import rate_limit
from ..templates_cfg import templates
from ..utils import descendant_folder_ids, get_or_create_tag, refresh_link_fts, sidebar_data


async def warm_img_cache() -> None:
    await asyncio.sleep(10)
    with Session(db_engine) as db:
        rows = db.exec(select(Link.favicon_url, Link.thumbnail_url)).all()
    urls: set = set()
    for row in rows:
        if row.favicon_url:
            urls.add(row.favicon_url)
        if row.thumbnail_url:
            urls.add(row.thumbnail_url)
    sem = asyncio.Semaphore(8)

    async def _fetch(url: str) -> None:
        async with sem:
            async with _img_cache_lock:
                cached = _img_cache.get(url)
                if cached and time.time() < cached[0]:
                    return
            try:
                resp = await _http_client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                    "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
                }, timeout=5)
                if resp.status_code != 200:
                    return
                if not _safe_url(str(resp.url)):
                    return
                ct = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                if not ct.startswith("image/"):
                    return
                async with _img_cache_lock:
                    _img_cache[url] = (time.time() + _IMG_CACHE_TTL, ct, resp.content)
                    if len(_img_cache) > _IMG_CACHE_MAX:
                        _img_cache.popitem(last=False)
            except Exception:
                pass

    url_list = list(urls)
    for i in range(0, len(url_list), 50):
        await asyncio.gather(*[_fetch(u) for u in url_list[i:i + 50]])
        await asyncio.sleep(0)


router = APIRouter()

_http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient) -> None:
    global _http_client
    _http_client = client


_IMG_CACHE_TTL = 86400
_IMG_CACHE_MAX = 1000
_img_cache: OrderedDict = OrderedDict()
_img_cache_lock = asyncio.Lock()

_DNS_CACHE_TTL = 600
_dns_cache: dict[str, tuple[float, bool]] = {}

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



async def _hostname_resolves_public(hostname: str) -> bool:
    """Anti-DNS-rebinding : vérifie que le hostname ne résout pas vers une IP privée."""
    now = time.time()
    cached = _dns_cache.get(hostname)
    if cached and now < cached[0]:
        return cached[1]
    try:
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
        result = all(not _is_private_host(r[4][0]) for r in results)
        _dns_cache[hostname] = (now + _DNS_CACHE_TTL, result)
        return result
    except OSError:
        return False


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
    _pre = urlparse(url)
    try:
        ipaddress.ip_address(_pre.hostname or "")
    except ValueError:
        if not await _hostname_resolves_public(_pre.hostname or ""):
            return {"title": "", "description": "", "favicon_url": ""}
    try:
        resp = await _http_client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3",
        }, timeout=5)
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


def _validate_folder_id(session: Session, user_id: int, raw: Optional[str]) -> Optional[int]:
    if not raw or not raw.strip().isdigit():
        return None
    fid = int(raw)
    folder = session.exec(
        select(Folder).where(Folder.user_id == user_id, Folder.id == fid)
    ).first()
    return fid if folder else None


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
    fts_link_ids: list[int] = []

    if q:
        escaped = _fts_escape(q)
        try:
            rows = session.execute(
                text("SELECT rowid FROM fts_links WHERE fts_links MATCH :q ORDER BY rank"),
                {"q": escaped},
            ).fetchall()
            fts_link_ids = [r[0] for r in rows]
            stmt = stmt.where(Link.id.in_(fts_link_ids)) if fts_link_ids else stmt.where(Link.id < 0)
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
        all_fldrs = list(session.exec(select(Folder).where(Folder.user_id == user.id)).all())
        fids = descendant_folder_ids(all_fldrs, group_id)
        stmt = stmt.where(Link.folder_id.in_(fids))

    total = session.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    page = max(1, min(page, max(1, (total + PER_PAGE - 1) // PER_PAGE)))
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)

    if fts_link_ids:
        all_matches = list(session.exec(stmt).all())
        rank_map = {id_: i for i, id_ in enumerate(fts_link_ids)}
        all_matches.sort(key=lambda l: rank_map.get(l.id, len(fts_link_ids)))
        links = all_matches[(page - 1) * PER_PAGE : page * PER_PAGE]
    else:
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
    folder_id: Optional[int] = None,
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
            "prefill_folder_id": folder_id,
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
    folder_id: Optional[str] = Form(default=None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not _safe_url(url):
        raise HTTPException(status_code=400, detail="URL invalide")

    existing = session.exec(
        select(Link).where(Link.user_id == user.id, Link.url == url)
    ).first()
    if existing:
        return RedirectResponse(url=f"/links/{existing.id}/edit?duplicate=1", status_code=303)

    link = Link(
        user_id=user.id,
        url=url,
        title=title or url,
        description=description,
        favicon_url="",
        thumbnail_url="",
        note=note,
        is_public=is_public is not None,
        folder_id=_validate_folder_id(session, user.id, folder_id),
    )
    session.add(link)
    session.flush()

    tag_names = [t.strip() for t in tags.split(",") if t.strip()][:MAX_TAGS_PER_LINK]
    link_tags = _get_or_create_tags(session, user.id, tag_names)
    for t in link_tags:
        session.add(LinkTagLink(link_id=link.id, tag_id=t.id))

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
    return templates.TemplateResponse(
        "links/form.html",
        {
            "request": request,
            "link": link,
            "prefill_url": "",
            "meta": {},
            **sidebar,
            "current_tags": current_tags,
            "user": user,
        },
    )


@router.post("/links/{link_id}/edit")
async def edit_link(
    request: Request,
    link_id: int,
    background_tasks: BackgroundTasks,
    url: str = Form(...),
    title: str = Form("", max_length=MAX_TITLE_LEN),
    description: str = Form("", max_length=MAX_DESC_LEN),
    note: str = Form("", max_length=MAX_NOTE_LEN),
    is_public: Optional[str] = Form(default=None),
    tags: str = Form(""),
    folder_id: Optional[str] = Form(default=None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    link = session.get(Link, link_id)
    if not link or link.user_id != user.id:
        raise HTTPException(status_code=404)

    if not _safe_url(url):
        raise HTTPException(status_code=400, detail="URL invalide")

    url_changed = url != link.url
    link.url = url
    link.title = title or url
    link.description = description
    link.note = note
    link.is_public = is_public is not None
    link.folder_id = _validate_folder_id(session, user.id, folder_id)
    link.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if url_changed:
        link.favicon_url = ""
        link.thumbnail_url = ""
    session.add(link)
    session.flush()

    session.execute(text("DELETE FROM link_tags WHERE link_id = :id"), {"id": link_id})
    session.flush()
    tag_names = [t.strip() for t in tags.split(",") if t.strip()][:MAX_TAGS_PER_LINK]
    link_tags = _get_or_create_tags(session, user.id, tag_names)
    for t in link_tags:
        session.add(LinkTagLink(link_id=link.id, tag_id=t.id))

    session.flush()
    refresh_link_fts(session, link, link_tags)
    session.commit()

    if url_changed:
        background_tasks.add_task(_fetch_and_update_meta, link.id, url)

    return RedirectResponse(url="/", status_code=303)


# ─── Delete ──────────────────────────────────────────────────────────────────

@router.post("/links/{link_id}/delete")
async def delete_link(
    link_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    unstar_freshrss: str = Form(""),
    return_to: str = Form("/"),
):
    from ..models import FreshRSSConfig
    from .freshrss import unstar_item
    link = session.get(Link, link_id)
    if not link or link.user_id != user.id:
        raise HTTPException(status_code=404)
    if unstar_freshrss and link.freshrss_item_id:
        config = session.exec(
            select(FreshRSSConfig).where(FreshRSSConfig.user_id == user.id)
        ).first()
        if config and config.freshrss_url:
            item_id = link.freshrss_item_id
            asyncio.create_task(unstar_item(config, item_id))
    session.delete(link)
    session.commit()
    redirect_url = return_to if return_to.startswith("/") else "/"
    return RedirectResponse(url=redirect_url, status_code=303)


# ─── Move (drag & drop sidebar) ──────────────────────────────────────────────

@router.post("/links/{link_id}/move")
async def move_link(
    link_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    link = session.get(Link, link_id)
    if not link or link.user_id != user.id:
        raise HTTPException(status_code=404)
    body = await request.json()
    raw_fid = body.get("folder_id")
    link.folder_id = _validate_folder_id(session, user.id, str(raw_fid)) if raw_fid else None
    session.add(link)
    session.commit()
    return {"ok": True, "folder_id": link.folder_id}


# ─── API metadata fetch ───────────────────────────────────────────────────────

@router.get("/api/fetch-meta", dependencies=[Depends(rate_limit(30, 60))])
async def api_fetch_meta(url: str, user: User = Depends(get_current_user)):
    return await _fetch_meta(url)


@router.get("/proxy/img", dependencies=[Depends(rate_limit(120, 60))])
async def proxy_image(request: Request, url: str, user: User = Depends(get_current_user)):
    if not _safe_url(url):
        raise HTTPException(status_code=400)
    etag = hashlib.md5(url.encode()).hexdigest()
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    cached = _img_cache.get(url)
    if cached:
        expiry, content_type, content = cached
        if time.time() < expiry:
            async with _img_cache_lock:
                _img_cache.move_to_end(url)
            if content is None:
                raise HTTPException(status_code=404)
            return Response(
                content=content,
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400", "ETag": etag, "Cross-Origin-Resource-Policy": "cross-origin"},
            )
        async with _img_cache_lock:
            if url in _img_cache:
                del _img_cache[url]

    _proxy_parsed = urlparse(url)
    try:
        ipaddress.ip_address(_proxy_parsed.hostname or "")
    except ValueError:
        if not await _hostname_resolves_public(_proxy_parsed.hostname or ""):
            raise HTTPException(status_code=400)
    try:
        resp = await _http_client.get(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
        }, timeout=5)
        if resp.status_code != 200:
            async with _img_cache_lock:
                _img_cache[url] = (time.time() + 3600, None, None)
                if len(_img_cache) > _IMG_CACHE_MAX:
                    _img_cache.popitem(last=False)
            raise HTTPException(status_code=404)
        final_url = str(resp.url)
        if not _safe_url(final_url):
            raise HTTPException(status_code=400)
        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=415)
        content = resp.content
        async with _img_cache_lock:
            _img_cache[url] = (time.time() + _IMG_CACHE_TTL, content_type, content)
            if len(_img_cache) > _IMG_CACHE_MAX:
                _img_cache.popitem(last=False)
        return Response(
            content=content,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=86400", "ETag": etag, "Cross-Origin-Resource-Policy": "cross-origin"},
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502)
