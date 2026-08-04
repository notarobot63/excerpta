import asyncio
import io
import json
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import qrcode
from bs4 import BeautifulSoup
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import or_, text
from sqlmodel import Session, select

from ..auth import get_current_user
from ..config import settings as cfg
from ..crypto import decrypt
from ..database import engine as db_engine, get_session
from ..demo import forbid_in_demo_dep
from ..models import Folder, Link, LinkTagLink, Tag, User
from ..ratelimit import rate_limit
from ..templates_cfg import templates
from ..utils import get_or_create_tag, refresh_link_fts, sidebar_data, slugify
from .links import _archive_many, _assert_public_url, _fetch_meta, _safe_stream, _safe_url

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
        folder_name = escape(lk.folder.name if lk.folder else "", quote=True)
        ts = int(lk.created_at.timestamp())
        title = escape(lk.title)
        url = escape(lk.url, quote=True)
        lines.append(
            f'    <DT><A HREF="{url}" ADD_DATE="{ts}" TAGS="{tags}" FOLDER="{folder_name}">{title}</A>'
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
    archiving: int = 0,
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
        request,
        "settings/index.html",
        {
            "user": user,
            "api_key_plain": decrypt(user.api_key),
            "bookmarklet": bookmarklet,
            "link_count": link_count,
            "archiving": archiving,
            **sidebar_data(session, user.id),
        },
        # La clé API figure en clair dans cette page : elle ne doit rester ni
        # dans le cache du navigateur ni dans celui d'un proxy intermédiaire.
        headers={"Cache-Control": "no-store"},
    )


# ── Page publique ────────────────────────────────────────────────────────────

@router.post("/settings/public-page", dependencies=[Depends(forbid_in_demo_dep)])
async def save_public_page(
    public_page_title: str = Form("", max_length=100),
    public_slug: str = Form("", max_length=64),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    user.public_page_title = public_page_title.strip() or "Liens publics"

    new_slug = slugify(public_slug)
    if new_slug != (user.public_slug or ""):
        if not new_slug:
            session.add(user)
            session.commit()
            return RedirectResponse(url="/settings?slug_error=empty", status_code=303)
        taken = session.exec(
            select(User).where(User.public_slug == new_slug, User.id != user.id)
        ).first()
        if taken:
            session.add(user)
            session.commit()
            return RedirectResponse(url="/settings?slug_error=taken", status_code=303)
        user.public_slug = new_slug

    session.add(user)
    session.commit()
    return RedirectResponse(url="/settings?saved=public", status_code=303)


# ── Organisation (tags / dossiers) ────────────────────────────────────────────

@router.post("/settings/organization")
async def save_organization(
    tags_enabled: Optional[str] = Form(None),
    folders_enabled: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    user.tags_enabled = tags_enabled is not None
    user.folders_enabled = folders_enabled is not None
    session.add(user)
    session.commit()
    return RedirectResponse(url="/settings?saved=organization", status_code=303)


# ── QR code Android ───────────────────────────────────────────────────────────

@router.get("/settings/android-qr.png", dependencies=[Depends(forbid_in_demo_dep)])
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

@router.post("/settings/refresh-metadata", dependencies=[Depends(rate_limit(2, 3600)), Depends(forbid_in_demo_dep)])
async def refresh_metadata(
    user: User = Depends(get_current_user),
):
    async def generate():
        with Session(db_engine) as session:
            rows = session.exec(
                select(Link.id, Link.url).where(
                    Link.user_id == user.id,
                    or_(
                        Link.thumbnail_url == None,
                        Link.thumbnail_url == "",
                        Link.description == None,
                        Link.description == "",
                    ),
                ).limit(500)
            ).all()
        candidates = [(r[0], r[1]) for r in rows]
        total = len(candidates)
        yield f"data: {json.dumps({'total': total, 'current': 0, 'updated': 0})}\n\n"
        updated = 0
        for i, (link_id, url) in enumerate(candidates):
            try:
                meta = await _fetch_meta(url)
                with Session(db_engine) as session:
                    link = session.get(Link, link_id)
                    if link:
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
                            session.commit()
                            updated += 1
            except Exception:
                pass
            yield f"data: {json.dumps({'total': total, 'current': i + 1, 'updated': updated})}\n\n"
        yield f"data: {json.dumps({'done': True, 'updated': updated})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Import ────────────────────────────────────────────────────────────────────

async def _refresh_new_links(link_ids: list[int]) -> None:
    for link_id in link_ids:
        try:
            meta = await _fetch_meta_by_id(link_id)
            if meta is None:
                continue
            with Session(db_engine) as session:
                link = session.get(Link, link_id)
                if not link:
                    continue
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
                    session.commit()
        except Exception:
            pass


async def _fetch_meta_by_id(link_id: int):
    with Session(db_engine) as session:
        link = session.get(Link, link_id)
        if not link:
            return None
        url = link.url
    return await _fetch_meta(url)


@router.get("/settings/import", response_class=HTMLResponse)
async def import_form(
    request: Request,
    imported: Optional[int] = None,
    skipped: Optional[int] = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "settings/import.html",
        {
            "user": user,
            "imported": imported,
            "skipped": skipped,
            **sidebar_data(session, user.id),
        },
    )


@router.post("/settings/import", dependencies=[Depends(rate_limit(5, 3600)), Depends(forbid_in_demo_dep)])
async def import_links(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    MAX = 10 * 1024 * 1024
    content = await file.read(MAX + 1)
    if len(content) > MAX:
        from fastapi import HTTPException
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
    snippet = content[:512].lower()
    if b"<!doctype" not in snippet and b"<html" not in snippet and b"<dl" not in snippet and b"<a href" not in snippet:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Unrecognised format, a Netscape HTML file is expected")
    items = _parse_netscape(content.decode("utf-8", errors="replace"))[:10_000]

    imported = skipped = 0
    existing_urls = {
        row[0]
        for row in session.execute(
            text("SELECT url FROM links WHERE user_id = :uid"), {"uid": user.id}
        ).fetchall()
    }

    new_link_ids = []

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
        new_link_ids.append(link.id)

    session.commit()

    if new_link_ids:
        background_tasks.add_task(_refresh_new_links, new_link_ids)

    return RedirectResponse(url=f"/settings/import?imported={imported}&skipped={skipped}", status_code=303)


# ── Purge FreshRSS ────────────────────────────────────────────────────────────

@router.post("/settings/purge-freshrss", dependencies=[Depends(rate_limit(3, 3600))])
async def purge_freshrss(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    config = session.exec(select(FreshRSSConfig).where(FreshRSSConfig.user_id == user.id)).first()
    if config:
        folder = session.exec(
            select(Folder).where(Folder.user_id == user.id, Folder.name == config.group_name)
        ).first()
        if folder:
            session.execute(
                text("DELETE FROM link_tags WHERE link_id IN "
                     "(SELECT id FROM links WHERE folder_id = :fid)"),
                {"fid": folder.id},
            )
            session.execute(
                text("DELETE FROM fts_links WHERE rowid IN "
                     "(SELECT id FROM links WHERE folder_id = :fid)"),
                {"fid": folder.id},
            )
            session.execute(
                text("DELETE FROM links WHERE user_id = :uid AND folder_id = :fid"),
                {"uid": user.id, "fid": folder.id},
            )
            session.delete(folder)
        config.synced_count = 0
        config.last_sync = None
        session.add(config)
    session.commit()
    return RedirectResponse(url="/settings?purged=freshrss", status_code=303)


# ── Rebuild FTS ───────────────────────────────────────────────────────────────

@router.post("/settings/rebuild-fts", dependencies=[Depends(rate_limit(3, 3600))])
async def rebuild_fts(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    links = list(session.exec(select(Link).where(Link.user_id == user.id)).all())
    session.execute(
        text("DELETE FROM fts_links WHERE rowid IN (SELECT id FROM links WHERE user_id = :uid)"),
        {"uid": user.id},
    )
    for link in links:
        tags = list(session.exec(
            select(Tag).join(LinkTagLink, LinkTagLink.tag_id == Tag.id)
            .where(LinkTagLink.link_id == link.id)
        ).all())
        refresh_link_fts(session, link, tags)
    session.commit()
    return RedirectResponse(url="/settings?fts_rebuilt=1", status_code=303)


# ── Purge tout ────────────────────────────────────────────────────────────────

@router.post("/settings/purge-all", dependencies=[Depends(rate_limit(1, 3600))])
async def purge_all(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    session.execute(text("DELETE FROM link_tags WHERE link_id IN (SELECT id FROM links WHERE user_id = :uid)"), {"uid": user.id})
    session.execute(text("DELETE FROM fts_links WHERE rowid IN (SELECT id FROM links WHERE user_id = :uid)"), {"uid": user.id})
    session.execute(text("DELETE FROM links WHERE user_id = :uid"), {"uid": user.id})
    session.execute(text("DELETE FROM tags WHERE user_id = :uid"), {"uid": user.id})
    session.execute(text("DELETE FROM folders WHERE user_id = :uid"), {"uid": user.id})
    session.commit()
    return RedirectResponse(url="/settings?purged=all", status_code=303)


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

_CHECKER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "fr,en;q=0.7",
}
_check_jobs: dict[int, dict] = {}  # user_id → {total, done, running}

# Codes ambigus (protection anti-bot probable) - ne pas marquer comme cassé
_AMBIGUOUS_CODES = {401, 403, 405, 406, 429}


async def _check_status(method: str, url: str) -> int:
    """Code de statut final, redirections revalidées saut par saut.

    Passe par `_safe_stream` plutôt que par une seconde implémentation locale de
    la même règle : le corps n'est jamais lu, seul le statut compte, et un
    vérificateur qui parcourt toute une collection n'a pas à télécharger les
    pages.
    """
    if not await _assert_public_url(url):
        raise ValueError("URL non autorisée (SSRF)")
    async with _safe_stream(method, url, timeout=10, headers=_CHECKER_HEADERS) as resp:
        return resp.status_code


async def _check_url(url: str) -> dict:
    try:
        status = await _check_status("HEAD", url)
        if status in _AMBIGUOUS_CODES or status >= 400:
            status = await _check_status("GET", url)
        broken = status >= 400 and status not in _AMBIGUOUS_CODES
        return {"status": status, "broken": broken, "error": None}
    except Exception as e:
        return {"status": None, "broken": True, "error": str(e)[:100]}


async def _run_check_background(user_id: int) -> None:
    _check_jobs[user_id] = {"total": 0, "done": 0, "running": True}
    try:
        with Session(db_engine) as s:
            link_ids = [lk.id for lk in s.exec(select(Link).where(Link.user_id == user_id)).all()]
        _check_jobs[user_id]["total"] = len(link_ids)
        sem = asyncio.Semaphore(5)

        async def _check_one(lid: int):
            async with sem:
                with Session(db_engine) as s:
                    lk = s.get(Link, lid)
                    url = lk.url if lk else None
                if not url:
                    _check_jobs[user_id]["done"] += 1
                    return
                result = await _check_url(url)
                with Session(db_engine) as s:
                    lk2 = s.get(Link, lid)
                    if lk2:
                        lk2.is_broken = result["broken"]
                        lk2.check_status = result["status"]
                        lk2.last_checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        s.add(lk2)
                        s.commit()
                _check_jobs[user_id]["done"] += 1
                await asyncio.sleep(0.2)

        async def _check_one_safe(lid: int):
            try:
                await asyncio.wait_for(_check_one(lid), timeout=30)
            except asyncio.TimeoutError:
                _check_jobs[user_id]["done"] += 1

        await asyncio.gather(*[_check_one_safe(lid) for lid in link_ids])
    finally:
        _check_jobs[user_id]["running"] = False


@router.get("/settings/check-links/status")
async def check_links_status(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    job = _check_jobs.get(user.id, {"total": 0, "done": 0, "running": False})
    broken_count = len(session.exec(select(Link).where(Link.user_id == user.id, Link.is_broken == True)).all())
    checked_count = len(session.exec(select(Link).where(Link.user_id == user.id, Link.last_checked_at != None)).all())
    return {
        "total": job["total"],
        "done": job["done"],
        "running": job["running"],
        "broken_count": broken_count,
        "checked_count": checked_count,
    }


@router.get("/settings/check-links", response_class=HTMLResponse)
async def check_links_form(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    job = _check_jobs.get(user.id, {"total": 0, "done": 0, "running": False})
    broken = list(session.exec(select(Link).where(Link.user_id == user.id, Link.is_broken == True)).all())
    checked_count = len(session.exec(select(Link).where(Link.user_id == user.id, Link.last_checked_at != None)).all())
    total_count = len(session.exec(select(Link).where(Link.user_id == user.id)).all())
    return templates.TemplateResponse(
        request,
        "settings/check_links.html",
        {
            "user": user,
            "job": job,
            "broken": broken,
            "checked_count": checked_count,
            "total_count": total_count,
            **sidebar_data(session, user.id),
        },
    )


@router.post("/settings/check-links", dependencies=[Depends(rate_limit(3, 3600)), Depends(forbid_in_demo_dep)])
async def check_links_run(
    user: User = Depends(get_current_user),
):
    if not _check_jobs.get(user.id, {}).get("running", False):
        asyncio.create_task(_run_check_background(user.id))
    return RedirectResponse("/settings/check-links", status_code=303)


# ── Archivage Wayback en masse ──────────────────────────────────────────────────
# La route unitaire POST /links/{id}/archive vit désormais dans routes/links.py
# (co-localisée avec _wayback_archive). Ici : archivage de tous les non-archivés.

@router.post("/settings/archive-all", dependencies=[Depends(forbid_in_demo_dep)])
async def archive_all(
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    links = session.exec(
        select(Link).where(Link.user_id == user.id, Link.archived_url.is_(None))
    ).all()
    ids = [lk.id for lk in links]
    for lk in links:
        lk.archive_status = "pending"
        session.add(lk)
    session.commit()
    if ids:
        background_tasks.add_task(_archive_many, ids)
    return RedirectResponse(url=f"/settings?archiving={len(ids)}", status_code=303)
