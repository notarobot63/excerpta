"""CRUD web des liens : liste, ajout, édition, suppression, bulk-delete, move."""
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, text
from sqlmodel import Session, select

from ...auth import get_current_user
from ...background import spawn
from ...database import get_session
from ...demo import (assert_link_quota, demo_active, demo_rate_limit,
                     is_demo_user, is_demo_user_id)
from ...models import Folder, Link, LinkTagLink, Tag, User
from ...ratelimit import rate_limit
from ...templates_cfg import templates
from ...utils import (descendant_folder_ids, get_or_create_tag, refresh_link_fts,
                      safe_next, sidebar_data)
from .archive import _wayback_archive
from .constants import MAX_DESC_LEN, MAX_NOTE_LEN, MAX_TAGS_PER_LINK, MAX_TITLE_LEN, PER_PAGE
from .enrichment import _fetch_and_update_meta, _fetch_meta
from .net_guard import _safe_url

router = APIRouter()

logger = logging.getLogger("excerpta.links.crud")


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


def _fts_escape(q: str) -> str:
    words = re.findall(r"\w+", q)
    if not words:
        return ""
    return " ".join(f"{w}*" for w in words)


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
            # bm25 pondéré : un match dans le titre pèse plus que dans l'URL.
            # Colonnes fts_links : title, description, note, url, tags.
            rows = session.execute(
                text(
                    "SELECT rowid FROM fts_links WHERE fts_links MATCH :q "
                    "ORDER BY bm25(fts_links, 10.0, 2.0, 2.0, 1.0, 4.0)"
                ),
                {"q": escaped},
            ).fetchall()
            fts_link_ids = [r[0] for r in rows]
            stmt = stmt.where(Link.id.in_(fts_link_ids)) if fts_link_ids else stmt.where(Link.id < 0)
        except Exception:
            logger.warning("Requête FTS en échec pour %r, repli sur LIKE", q, exc_info=True)
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

    context = {
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
        # URL de la vue courante SANS partial=1 : sert de return_to aux formulaires
        # (sinon une suppression depuis une page chargée en AJAX redirige vers le
        #  fragment nu /?page=N&partial=1).
        "current_url": "/?" + _qs(page),
    }

    # Recherche temps réel / pagination AJAX : fragment seul, sans le layout
    is_partial = request.query_params.get("partial") or request.headers.get("X-Partial")
    template = "links/_results.html" if is_partial else "links/list.html"
    return templates.TemplateResponse(request, template, context)


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
        request,
        "links/form.html",
        {
            "link": None,
            "prefill_url": url or "",
            "meta": meta,
            "prefill_folder_id": folder_id,
            **sidebar,
            "user": user,
        },
    )


def create_link(
    session: Session,
    background_tasks: BackgroundTasks,
    *,
    user_id: int,
    url: str,
    title: str = "",
    description: str = "",
    note: str = "",
    is_public: bool = False,
    folder_id: Optional[int] = None,
    tag_names: Optional[List[str]] = None,
) -> tuple[Link, bool]:
    """Crée un lien et planifie son enrichissement (métadonnées + archivage Wayback).

    Chemin de création unique partagé par l'UI web et l'API mobile, pour garantir
    qu'un lien ajouté par n'importe quelle source reçoive titre/extrait/favicon et
    archivage de façon identique.

    Retourne (link, created). Si un lien avec la même URL existe déjà pour cet
    utilisateur, renvoie l'existant avec created=False sans rien planifier.
    Le caller doit avoir validé `_safe_url(url)` et résolu `folder_id`.
    """
    existing = session.exec(
        select(Link).where(Link.user_id == user_id, Link.url == url)
    ).first()
    if existing:
        return existing, False

    # En démo, le lien est enrichi comme en production (la garde SSRF de
    # net_guard couvre l'URL du visiteur), mais rien ne devient public et rien
    # n'est poussé chez Wayback. Voir app/demo.py pour le contrat complet.
    demo = demo_active() and is_demo_user_id(session, user_id)

    link = Link(
        user_id=user_id,
        url=url,
        title=title or url,
        description=description,
        favicon_url="",
        thumbnail_url="",
        note=note,
        is_public=False if demo else is_public,
        folder_id=folder_id,
        archive_status=None if demo else "pending",
    )
    session.add(link)
    session.flush()

    link_tags = _get_or_create_tags(session, user_id, (tag_names or [])[:MAX_TAGS_PER_LINK])
    for t in link_tags:
        session.add(LinkTagLink(link_id=link.id, tag_id=t.id))

    session.flush()
    refresh_link_fts(session, link, link_tags)
    session.commit()

    background_tasks.add_task(_fetch_and_update_meta, link.id, url)
    if not demo:
        background_tasks.add_task(_wayback_archive, link.id)
    return link, True


@router.post("/links/add", dependencies=[Depends(demo_rate_limit(40, 3600))])
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
        raise HTTPException(status_code=400, detail="Invalid URL")
    assert_link_quota(session, user.id)

    tag_names = [t.strip() for t in tags.split(",") if t.strip()]
    link, created = create_link(
        session,
        background_tasks,
        user_id=user.id,
        url=url,
        title=title,
        description=description,
        note=note,
        is_public=is_public is not None,
        folder_id=_validate_folder_id(session, user.id, folder_id),
        tag_names=tag_names,
    )
    if not created:
        return RedirectResponse(url=f"/links/{link.id}/edit?duplicate=1", status_code=303)

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
        request,
        "links/form.html",
        {
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
        raise HTTPException(status_code=400, detail="Invalid URL")

    demo = demo_active() and is_demo_user(user)
    old_folder_id = link.folder_id
    fr_item_id = link.freshrss_item_id
    url_changed = url != link.url
    link.url = url
    link.title = title or url
    link.description = description
    link.note = note
    link.is_public = False if demo else (is_public is not None)
    new_folder_id = _validate_folder_id(session, user.id, folder_id)
    link.folder_id = new_folder_id
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

    _maybe_unstar_on_leave(session, user.id, fr_item_id, old_folder_id, new_folder_id)

    if url_changed:
        background_tasks.add_task(_fetch_and_update_meta, link.id, url)

    return RedirectResponse(url="/", status_code=303)


# ─── Delete ──────────────────────────────────────────────────────────────────

@router.post("/links/{link_id}/delete")
async def delete_link(
    link_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    unstar_freshrss: str = Form(""),
    return_to: str = Form("/"),
):
    from ...models import FreshRSSConfig
    from ..freshrss import unstar_item
    link = session.get(Link, link_id)
    if not link or link.user_id != user.id:
        raise HTTPException(status_code=404)
    if unstar_freshrss and link.freshrss_item_id:
        config = session.exec(
            select(FreshRSSConfig).where(FreshRSSConfig.user_id == user.id)
        ).first()
        if config and config.freshrss_url:
            item_id = link.freshrss_item_id
            spawn(unstar_item(config, item_id), name=f"unstar-{item_id}")
    session.delete(link)
    session.commit()
    # Appel AJAX (suppression optimiste depuis la liste) : pas de redirect,
    # le client retire la carte du DOM lui-même → évite le rechargement complet.
    if request.headers.get("x-csrf-token"):
        return Response(status_code=204)
    redirect_url = safe_next(return_to)
    return RedirectResponse(url=redirect_url, status_code=303)


# ─── Bulk delete ─────────────────────────────────────────────────────────────

@router.post("/links/bulk-delete")
async def bulk_delete_links(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    form = await request.form()
    link_ids = [int(v) for v in form.getlist("link_ids") if str(v).isdigit()]
    return_to = str(form.get("return_to", "/"))
    if link_ids:
        links = session.exec(select(Link).where(Link.id.in_(link_ids), Link.user_id == user.id)).all()
        for lk in links:
            session.delete(lk)
        session.commit()
    if request.headers.get("x-csrf-token"):
        return Response(status_code=204)
    redirect_url = safe_next(return_to)
    return RedirectResponse(url=redirect_url, status_code=303)


def _maybe_unstar_on_leave(
    session: Session, user_id: int, item_id: Optional[str],
    old_folder_id: Optional[int], new_folder_id: Optional[int],
) -> None:
    """Désétoile le lien sur FreshRSS s'il quitte le dossier FreshRSS.

    Conditions : le lien a un freshrss_item_id, il était dans le dossier
    FreshRSS (nommé config.group_name) et il change de dossier.
    Désétoilage fire-and-forget, cohérent avec la suppression.
    """
    from ...models import FreshRSSConfig
    from ..freshrss import unstar_item
    if not item_id or old_folder_id is None or old_folder_id == new_folder_id:
        return
    config = session.exec(
        select(FreshRSSConfig).where(FreshRSSConfig.user_id == user_id)
    ).first()
    if not (config and config.freshrss_url and config.group_name):
        return
    fr_folder = session.exec(
        select(Folder).where(Folder.user_id == user_id, Folder.name == config.group_name)
    ).first()
    if fr_folder and old_folder_id == fr_folder.id:
        spawn(unstar_item(config, item_id), name=f"unstar-{item_id}")


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
    old_folder_id = link.folder_id
    item_id = link.freshrss_item_id
    new_folder_id = _validate_folder_id(session, user.id, str(raw_fid)) if raw_fid else None
    link.folder_id = new_folder_id
    session.add(link)
    session.commit()
    _maybe_unstar_on_leave(session, user.id, item_id, old_folder_id, new_folder_id)
    return {"ok": True, "folder_id": new_folder_id}


# ─── Bulk move / bulk tag ─────────────────────────────────────────────────────

@router.post("/links/bulk-move")
async def bulk_move_links(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    body = await request.json()
    link_ids = [int(v) for v in (body.get("link_ids") or []) if str(v).isdigit()]
    raw_fid = body.get("folder_id")
    new_folder_id = _validate_folder_id(session, user.id, str(raw_fid)) if raw_fid else None
    if link_ids:
        links = session.exec(
            select(Link).where(Link.id.in_(link_ids), Link.user_id == user.id)
        ).all()
        for lk in links:
            old_folder_id = lk.folder_id
            item_id = lk.freshrss_item_id
            lk.folder_id = new_folder_id
            session.add(lk)
            session.commit()
            _maybe_unstar_on_leave(session, user.id, item_id, old_folder_id, new_folder_id)
    return {"ok": True, "folder_id": new_folder_id}


@router.post("/links/bulk-tag")
async def bulk_tag_links(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Ajoute les tags donnés aux liens sélectionnés (mode ajout uniquement :
    union avec les tags existants de chaque lien, jamais un remplacement — un
    "replace" en masse sur des liens ayant des tags différents serait
    destructeur et surprenant, contrairement à l'édition d'un lien seul)."""
    body = await request.json()
    link_ids = [int(v) for v in (body.get("link_ids") or []) if str(v).isdigit()]
    tag_names = [t.strip() for t in str(body.get("tags", "")).split(",") if t.strip()][:MAX_TAGS_PER_LINK]
    if link_ids and tag_names:
        new_tags = _get_or_create_tags(session, user.id, tag_names)
        links = session.exec(
            select(Link).where(Link.id.in_(link_ids), Link.user_id == user.id)
        ).all()
        for lk in links:
            existing_tags = list(lk.tags)
            existing_tag_ids = {t.id for t in existing_tags}
            added = [t for t in new_tags if t.id not in existing_tag_ids]
            for t in added:
                session.add(LinkTagLink(link_id=lk.id, tag_id=t.id))
            session.flush()
            refresh_link_fts(session, lk, existing_tags + added)
        session.commit()
    return {"ok": True, "tags": tag_names}


# ─── API metadata fetch ───────────────────────────────────────────────────────

@router.get("/api/fetch-meta", dependencies=[Depends(rate_limit(30, 60))])
async def api_fetch_meta(
    url: str, user: User = Depends(get_current_user), session: Session = Depends(get_session)
):
    # Libère la connexion DB avant l'appel HTTP externe (lent) : sinon une rafale
    # de requêtes sature le pool SQLAlchemy (5+10) et affame les autres routes.
    session.close()
    return await _fetch_meta(url)
