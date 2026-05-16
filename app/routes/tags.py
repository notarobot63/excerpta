from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlmodel import Session

from ..auth import get_current_user
from ..database import get_session
from ..models import Tag, User
from ..templates_cfg import templates
from ..utils import sidebar_data

router = APIRouter()


@router.get("/tags", response_class=HTMLResponse)
async def list_tags(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        "tags/list.html",
        {"request": request, "user": user, **sidebar_data(session, user.id)},
    )


@router.post("/tags/{tag_id}/delete")
async def delete_tag(
    tag_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    tag = session.get(Tag, tag_id)
    if not tag or tag.user_id != user.id:
        raise HTTPException(status_code=404)
    session.execute(
        text("DELETE FROM fts_links WHERE link_id IN (SELECT link_id FROM link_tags WHERE tag_id = :tid)"),
        {"tid": tag_id},
    )
    session.execute(text("DELETE FROM link_tags WHERE tag_id = :id"), {"id": tag_id})
    session.delete(tag)
    session.commit()
    return RedirectResponse(url="/tags", status_code=303)
