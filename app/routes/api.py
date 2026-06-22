from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlmodel import Session, select
from datetime import datetime, timezone
from typing import List, Optional

from ..crypto import hmac_key
from ..database import get_session
from ..models import Folder, Link, LinkTagLink, Tag, User
from ..ratelimit import rate_limit
from ..utils import descendant_folder_ids, get_or_create_tag, refresh_link_fts
from .links import MAX_TAGS_PER_LINK, _extract_reader, _fts_escape, _safe_url

router = APIRouter(prefix="/api/v1")

_api_rate_limit = rate_limit(calls=60, period_seconds=60)


async def _get_api_user(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    session: Session = Depends(get_session),
) -> User:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Authentification requise")
    await _api_rate_limit(request)
    computed_hmac = hmac_key(x_api_key)
    user = session.exec(select(User).where(User.api_key_hmac == computed_hmac)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Clé API invalide")
    return user


@router.get("/me")
async def api_me(user: User = Depends(_get_api_user)):
    return {"id": user.id, "name": user.name}


class LinkIn(BaseModel):
    url: str
    title: str = Field("", max_length=500)
    note: str = Field("", max_length=50_000)
    tags: List[str] = []
    folder_id: Optional[int] = None
    is_public: bool = False


@router.post("/links", status_code=201)
async def api_add_link(
    body: LinkIn,
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    if not _safe_url(body.url):
        raise HTTPException(status_code=400, detail="URL invalide")

    validated_folder_id: Optional[int] = None
    if body.folder_id is not None:
        folder = session.exec(
            select(Folder).where(Folder.user_id == user.id, Folder.id == body.folder_id)
        ).first()
        validated_folder_id = body.folder_id if folder else None

    link = Link(
        user_id=user.id,
        url=body.url,
        title=body.title or body.url,
        note=body.note,
        description="",
        favicon_url="",
        is_public=body.is_public,
        folder_id=validated_folder_id,
    )
    session.add(link)
    session.flush()

    tag_names = [t.strip().lower() for t in body.tags if t.strip()][:MAX_TAGS_PER_LINK]
    link_tags = [get_or_create_tag(session, user.id, n) for n in tag_names]
    for t in link_tags:
        session.add(LinkTagLink(link_id=link.id, tag_id=t.id))

    session.flush()
    refresh_link_fts(session, link, link_tags)
    session.commit()

    return {"id": link.id, "url": link.url, "title": link.title}


@router.get("/links")
async def api_list_links(
    q: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    group_id: Optional[int] = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=30, ge=1, le=100),
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    stmt = select(Link).where(Link.user_id == user.id)

    if tag:
        tag_obj = session.exec(
            select(Tag).where(Tag.user_id == user.id, Tag.name == tag.lower())
        ).first()
        if not tag_obj:
            return {"links": [], "total": 0, "page": page, "per_page": per_page, "total_pages": 1}
        stmt = (
            select(Link)
            .join(LinkTagLink, LinkTagLink.link_id == Link.id)
            .where(Link.user_id == user.id, LinkTagLink.tag_id == tag_obj.id)
        )
    elif group_id:
        all_fldrs = list(session.exec(select(Folder).where(Folder.user_id == user.id)).all())
        fids = descendant_folder_ids(all_fldrs, group_id)
        stmt = select(Link).where(Link.user_id == user.id, Link.folder_id.in_(fids))

    if q:
        escaped = _fts_escape(q)
        try:
            fts_rows = session.execute(
                text("SELECT rowid FROM fts_links WHERE fts_links MATCH :q ORDER BY rank"),
                {"q": escaped},
            ).fetchall()
            fts_ids = [r[0] for r in fts_rows]
            stmt = stmt.where(Link.id.in_(fts_ids)) if fts_ids else stmt.where(Link.id < 0)
        except Exception:
            q_like = f"%{q}%"
            stmt = stmt.where(
                Link.title.ilike(q_like) | Link.description.ilike(q_like) | Link.url.ilike(q_like)
            )

    stmt = stmt.order_by(Link.created_at.desc())

    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    links = session.exec(stmt.offset((page - 1) * per_page).limit(per_page)).all()

    return {
        "links": [
            {
                "id": lnk.id,
                "url": lnk.url,
                "title": lnk.title,
                "description": lnk.description,
                "favicon_url": lnk.favicon_url,
                "thumbnail_url": lnk.thumbnail_url,
                "note": lnk.note,
                "is_public": lnk.is_public,
                "archived_url": lnk.archived_url,
                "archive_status": lnk.archive_status,
                "is_broken": lnk.is_broken,
                "check_status": lnk.check_status,
                "has_reader": bool(lnk.reader_html) and not lnk.reader_failed,
                "created_at": lnk.created_at.isoformat(),
                "tags": [t.name for t in lnk.tags],
            }
            for lnk in links
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


@router.get("/tags")
async def api_list_tags(
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        text("""
            SELECT t.name, COUNT(lt.link_id) AS cnt
            FROM tags t
            LEFT JOIN link_tags lt ON lt.tag_id = t.id
            WHERE t.user_id = :uid
            GROUP BY t.id, t.name
            ORDER BY t.name
        """),
        {"uid": user.id},
    ).fetchall()
    return {"tags": [{"name": r[0], "count": r[1]} for r in rows]}


@router.get("/folders")
async def api_list_folders(
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        text("""
            SELECT f.id, f.name, f.parent_id, f.sort_order, COUNT(l.id) AS cnt
            FROM folders f
            LEFT JOIN links l ON l.folder_id = f.id
            WHERE f.user_id = :uid
            GROUP BY f.id, f.name, f.parent_id, f.sort_order
            ORDER BY f.sort_order, f.name
        """),
        {"uid": user.id},
    ).fetchall()

    folders = [{"id": r[0], "name": r[1], "parent_id": r[2], "sort_order": r[3], "count": r[4]} for r in rows]

    result: list = []

    def add_folder(f, d):
        result.append({**f, "depth": d})
        children = sorted(
            [x for x in folders if x["parent_id"] == f["id"]],
            key=lambda x: (x["sort_order"], x["name"]),
        )
        for child in children:
            add_folder(child, d + 1)

    for root in sorted(
        [f for f in folders if f["parent_id"] is None],
        key=lambda x: (x["sort_order"], x["name"]),
    ):
        add_folder(root, 0)

    return {"folders": result}


@router.get("/groups")
async def api_list_groups_compat(
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    """Alias de compatibilité → /api/v1/folders"""
    return await api_list_folders(user=user, session=session)


class LinkPatch(BaseModel):
    is_public: Optional[bool] = None


@router.patch("/links/{link_id}")
async def api_patch_link(
    link_id: int = Path(..., ge=1),
    body: LinkPatch = Body(...),
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    link = session.exec(
        select(Link).where(Link.id == link_id, Link.user_id == user.id)
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Lien introuvable")
    if body.is_public is not None:
        link.is_public = body.is_public
    session.add(link)
    session.commit()
    return {"id": link.id, "is_public": link.is_public}


@router.get("/links/{link_id}/reader")
async def api_link_reader(
    link_id: int = Path(..., ge=1),
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    link = session.exec(
        select(Link).where(Link.id == link_id, Link.user_id == user.id)
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Lien introuvable")
    # Extraction paresseuse : si le reader n'a jamais été généré (lien ajouté
    # avant la feature, ou jamais ouvert côté web), on l'extrait à la volée —
    # même comportement que la vue lecteur web (links.read_link).
    if not link.reader_html:
        data = await _extract_reader(link.url)
        if data and data["html"]:
            link.reader_title = (data["title"] or link.title or "")[:500]
            link.reader_html = data["html"]
            link.reader_failed = False
        else:
            link.reader_failed = True
        link.reader_extracted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(link)
        session.commit()
        session.refresh(link)
    if not link.reader_html:
        raise HTTPException(status_code=404, detail="Vue lecteur indisponible")
    return {
        "id": link.id,
        "reader_title": link.reader_title or link.title,
        "reader_html": link.reader_html,
        "reader_extracted_at": link.reader_extracted_at.isoformat()
        if link.reader_extracted_at
        else None,
    }


@router.delete("/links/{link_id}", status_code=204)
async def api_delete_link(
    link_id: int = Path(..., ge=1),
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    link = session.exec(
        select(Link).where(Link.id == link_id, Link.user_id == user.id)
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="Lien introuvable")
    session.delete(link)
    session.commit()
