from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import Session, select
from typing import List, Optional

from ..database import get_session
from ..models import Link, LinkGroupLink, LinkTagLink, Tag, Group, User
from ..ratelimit import rate_limit
from ..utils import descendant_group_ids, get_or_create_tag, refresh_link_fts
from .links import MAX_TAGS_PER_LINK, _safe_url

router = APIRouter(prefix="/api/v1")

_api_rate_limit = rate_limit(calls=60, period_seconds=60)


async def _get_api_user(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    session: Session = Depends(get_session),
) -> User:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Authentification requise")
    _api_rate_limit(request)
    user = session.exec(select(User).where(User.api_key == x_api_key)).first()
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


@router.post("/links", status_code=201)
async def api_add_link(
    body: LinkIn,
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    if not _safe_url(body.url):
        raise HTTPException(status_code=400, detail="URL invalide")

    link = Link(
        user_id=user.id,
        url=body.url,
        title=body.title or body.url,
        note=body.note,
        description="",
        favicon_url="",
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
        all_grps = list(session.exec(select(Group).where(Group.user_id == user.id)).all())
        gids = descendant_group_ids(all_grps, group_id)
        stmt = (
            select(Link)
            .join(LinkGroupLink, LinkGroupLink.link_id == Link.id)
            .where(Link.user_id == user.id, LinkGroupLink.group_id.in_(gids))
            .distinct()
        )

    if q:
        q_like = f"%{q}%"
        stmt = stmt.where(
            Link.title.ilike(q_like) | Link.description.ilike(q_like) | Link.url.ilike(q_like)
        )

    stmt = stmt.order_by(Link.created_at.desc())

    total = len(session.exec(stmt).all())
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


@router.get("/groups")
async def api_list_groups(
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(
        text("""
            SELECT g.id, g.name, g.parent_id, COUNT(lg.link_id) AS cnt
            FROM groups g
            LEFT JOIN link_groups lg ON lg.group_id = g.id
            WHERE g.user_id = :uid
            GROUP BY g.id, g.name, g.parent_id
            ORDER BY g.name
        """),
        {"uid": user.id},
    ).fetchall()

    groups = [{"id": r[0], "name": r[1], "parent_id": r[2], "count": r[3]} for r in rows]

    result: list = []

    def add_group(g, d):
        result.append({**g, "depth": d})
        for child in sorted([x for x in groups if x["parent_id"] == g["id"]], key=lambda x: x["name"]):
            add_group(child, d + 1)

    for root in sorted([g for g in groups if g["parent_id"] is None], key=lambda x: x["name"]):
        add_group(root, 0)

    return {"groups": result}


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
