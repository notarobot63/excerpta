import asyncio
import io
import json
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse
from .links import _safe_url

import httpx
import qrcode
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import text
from sqlmodel import Session, select

from ..auth import get_current_user
from ..config import settings as cfg
from ..crypto import decrypt
from ..database import get_session
from ..models import Group, Link, LinkGroupLink, LinkTagLink, Tag, User
from ..ratelimit import rate_limit
from ..templates_cfg import templates
from ..utils import get_or_create_tag, refresh_link_fts, sidebar_data
from .links import _fetch_meta

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────



def _parse_netscape(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.find_all("a"):
        href = a.get("href", "").strip()
        if not href.startswith("http"):
            continue
        tags_raw = a.get("tags", "") or a.get("tag", "")
        # Sépare par virgule puis par espace (tags Linkding parfois multi-mots)
        tags = []
        for part in tags_raw.split(","):
            tags.extend(w.lower() for w in part.split() if w.strip())
        note = ""
        parent = a.parent
        if parent:
            nxt = parent.find_next_sibling()
            if nxt and nxt.name == "dd":
                note = nxt.get_text(" ", strip=True)
        parsed = urlparse(href)
        favicon = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
        items.append({
            "url": href,
            "title": (a.get_text(strip=True) or href)[:500],
            "tags": tags,
            "note": note[:50_000],
            "favicon_url": favicon,
        })
    return items


def _build_netscape(links: list[Link]) -> str:
    from html import escape
    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Excerpta Export</TITLE>",
        "<H1>Bookmarks</H1>",
        "<DL><p>",
    ]
    for lk in links:
        tags = escape(",".join(t.name for t in lk.tags), quote=True)
        groups = escape(",".join(g.name for g in lk.groups), quote=True)
        ts = int(lk.created_at.timestamp())
        title = escape(lk.title)
        url = escape(lk.url, quote=True)
        lines.append(
            f'    <DT><A HREF="{url}" ADD_DATE="{ts}" TAGS="{tags}" GROUPS="{groups}">{title}</A>'
        )
        body = lk.note or lk.description
        if body:
            lines.append(f"    <DD>{escape(body)}")
    lines.append("</DL><p>")
    return "\n".join(lines)


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    bookmarklet = (
        "javascript:(function(){"
        f"window.open('{cfg.base_url}/links/add?url='+encodeURIComponent(window.location.href)"
        "+'&title='+encodeURIComponent(document.title),'_blank')"
        "})();"
    )
    link_count = session.execute(
        text("SELECT COUNT(*) FROM links WHERE user_id = :uid"), {"uid": user.id}
    ).scalar()
    return templates.TemplateResponse(
        "settings/index.html",
        {
            "request": request,
            "user": user,
            "api_key_plain": decrypt(user.api_key),
            "bookmarklet": bookmarklet,
            "link_count": link_count,
            **sidebar_data(session, user.id),
        },
    )


# ── QR code Android ───────────────────────────────────────────────────────────

@router.get("/settings/android-qr.png")
async def android_qr(
    user: User = Depends(get_current_user),
):
    data = json.dumps({"server": cfg.base_url, "key": decrypt(user.api_key)})
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


# ── Refresh métadonnées ────────────────────────────────────────────────────────

@router.post("/settings/refresh-metadata", dependencies=[Depends(rate_limit(2, 3600))])
async def refresh_metadata(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    links = list(session.exec(select(Link).where(Link.user_id == user.id)).all())
    updated = 0
    for link in links:
        if link.thumbnail_url and link.description:
            continue
        try:
            meta = await _fetch_meta(link.url)
            changed = False
            if not link.thumbnail_url and meta.get("thumbnail_url"):
                link.thumbnail_url = meta["thumbnail_url"]
                changed = True
            if not link.description and meta.get("description"):
                link.description = meta["description"]
                changed = True
            if not link.favicon_url and meta.get("favicon_url"):
                link.favicon_url = meta["favicon_url"]
                changed = True
            if changed:
                session.add(link)
                updated += 1
        except Exception:
            continue
    session.commit()
    return RedirectResponse(url=f"/settings?refreshed={updated}", status_code=303)


# ── Import ────────────────────────────────────────────────────────────────────

@router.get("/settings/import", response_class=HTMLResponse)
async def import_form(
    request: Request,
    imported: Optional[int] = None,
    skipped: Optional[int] = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        "settings/import.html",
        {
            "request": request,
            "user": user,
            "imported": imported,
            "skipped": skipped,
            **sidebar_data(session, user.id),
        },
    )


@router.post("/settings/import", dependencies=[Depends(rate_limit(5, 3600))])
async def import_links(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    MAX = 10 * 1024 * 1024
    content = await file.read(MAX + 1)
    if len(content) > MAX:
        from fastapi import HTTPException
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 10 Mo)")
    snippet = content[:512].lower()
    if b"<!doctype" not in snippet and b"<html" not in snippet and b"<dl" not in snippet and b"<a href" not in snippet:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Format non reconnu - fichier HTML Netscape attendu")
    items = _parse_netscape(content.decode("utf-8", errors="replace"))[:10_000]

    imported = skipped = 0
    existing_urls = {
        row[0]
        for row in session.execute(
            text("SELECT url FROM links WHERE user_id = :uid"), {"uid": user.id}
        ).fetchall()
    }

    for item in items:
        if item["url"] in existing_urls or not _safe_url(item["url"]):
            skipped += 1
            continue

        link = Link(
            user_id=user.id,
            url=item["url"],
            title=item["title"],
            note=item["note"],
            description="",
            favicon_url=item["favicon_url"],
        )
        session.add(link)
        session.flush()

        tags = [get_or_create_tag(session, user.id, n) for n in item["tags"]]
        for t in tags:
            session.add(LinkTagLink(link_id=link.id, tag_id=t.id))
        session.flush()

        refresh_link_fts(session, link, tags)
        existing_urls.add(item["url"])
        imported += 1

    session.commit()
    return RedirectResponse(url=f"/settings/import?imported={imported}&skipped={skipped}", status_code=303)


# ── Export ────────────────────────────────────────────────────────────────────

@router.get("/settings/export")
async def export_links(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    links = list(session.exec(select(Link).where(Link.user_id == user.id).order_by(Link.created_at.desc())).all())
    content = _build_netscape(links)
    filename = f"excerpta-export-{datetime.now(timezone.utc).strftime('%Y%m%d')}.html"
    return Response(
        content=content.encode("utf-8"),
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Broken link checker ───────────────────────────────────────────────────────

async def _check_link(client: httpx.AsyncClient, link) -> dict:
    try:
        resp = await client.head(link.url, follow_redirects=True, timeout=10)
        if resp.status_code >= 400:
            resp = await client.get(link.url, follow_redirects=True, timeout=10)
        return {"link": link, "status": resp.status_code, "broken": resp.status_code >= 400, "error": None}
    except Exception as e:
        return {"link": link, "status": None, "broken": True, "error": str(e)[:100]}


async def _check_all(links) -> list[dict]:
    sem = asyncio.Semaphore(5)

    async def _guarded(client, link):
        async with sem:
            result = await _check_link(client, link)
            await asyncio.sleep(0.2)
            return result

    headers = {"User-Agent": "Mozilla/5.0 (compatible; Excerpta/1.0; link-checker)"}
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=headers) as client:
        return await asyncio.gather(*[_guarded(client, lk) for lk in links])


@router.get("/settings/check-links", response_class=HTMLResponse)
async def check_links_form(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        "settings/check_links.html",
        {"request": request, "user": user, "results": None, **sidebar_data(session, user.id)},
    )


@router.post("/settings/check-links", response_class=HTMLResponse,
             dependencies=[Depends(rate_limit(3, 3600))])
async def check_links_run(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    links = list(session.exec(select(Link).where(Link.user_id == user.id)).all())
    results = await _check_all(links)
    broken = [r for r in results if r["broken"]]
    return templates.TemplateResponse(
        "settings/check_links.html",
        {
            "request": request,
            "user": user,
            "results": results,
            "broken": broken,
            "total": len(links),
            **sidebar_data(session, user.id),
        },
    )


# ── Archive Internet Archive ───────────────────────────────────────────────────

@router.post("/links/{link_id}/archive")
async def archive_link(
    link_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    link = session.get(Link, link_id)
    if not link or link.user_id != user.id:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            resp = await client.post(
                f"https://web.archive.org/save/{link.url}",
                headers={"User-Agent": "Excerpta/1.0"},
            )
        location = resp.headers.get("location", "") or resp.headers.get("content-location", "")
        if location:
            archived = location if location.startswith("http") else f"https://web.archive.org{location}"
        else:
            archived = f"https://web.archive.org/web/*/{link.url}"
        link.archived_url = archived
        link.archived_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(link)
        session.commit()
    except Exception:
        pass

    return RedirectResponse(url=f"/links/{link_id}/edit", status_code=303)
