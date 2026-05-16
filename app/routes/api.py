from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select
from typing import List

from ..database import get_session
from ..models import Link, LinkTagLink, User
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
