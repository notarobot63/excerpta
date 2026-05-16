from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select
from typing import List, Optional

from ..database import get_session
from ..models import Link, LinkGroupLink, LinkTagLink, Tag, Group, User
from ..utils import get_or_create_tag, refresh_link_fts
from .links import MAX_TAGS_PER_LINK, _safe_url

router = APIRouter(prefix="/api/v1")


async def _get_api_user(
    x_api_key: str = Header(..., alias="X-API-Key"),
    session: Session = Depends(get_session),
) -> User:
    user = session.exec(select(User).where(User.api_key == x_api_key)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Clé API invalide")
    return user


@router.get("/me")
async def api_me(user: User = Depends(_get_api_user)):
    return {"id": user.id, "name": user.name, "email": user.email}


class LinkIn(BaseModel):
    url: str
    title: str = ""
    note: str = ""
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
        stmt = (
            select(Link)
            .join(LinkGroupLink, LinkGroupLink.link_id == Link.id)
            .where(Link.user_id == user.id, LinkGroupLink.group_id == group_id)
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
    tags = session.exec(select(Tag).where(Tag.user_id == user.id).order_by(Tag.name)).all()
    return {"tags": [{"name": t.name, "count": len(t.links)} for t in tags]}


@router.get("/groups")
async def api_list_groups(
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    groups = session.exec(select(Group).where(Group.user_id == user.id).order_by(Group.name)).all()
    id_map = {g.id: g for g in groups}

    def depth(g):
        d, cur = 0, g
        while cur.parent_id and cur.parent_id in id_map:
            d += 1
            cur = id_map[cur.parent_id]
        return d

    result: list = []

    def add_group(g, d):
        result.append({"id": g.id, "name": g.name, "parent_id": g.parent_id,
                        "count": len(g.links), "depth": d})
        for child in sorted([x for x in groups if x.parent_id == g.id], key=lambda x: x.name):
            add_group(child, d + 1)

    for root in sorted([g for g in groups if g.parent_id is None], key=lambda x: x.name):
        add_group(root, 0)

    return {"groups": result}
