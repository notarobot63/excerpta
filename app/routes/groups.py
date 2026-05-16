from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlmodel import Session, select

from ..auth import get_current_user
from ..database import get_session
from ..models import Group, User
from ..templates_cfg import templates
from ..utils import sidebar_data

router = APIRouter()


def _validate_parent(session: Session, parent_id: Optional[int], user_id: int, exclude_id: Optional[int] = None) -> Optional[int]:
    if not parent_id:
        return None
    if parent_id == exclude_id:
        return None
    parent = session.get(Group, parent_id)
    if not parent or parent.user_id != user_id:
        return None
    return parent_id


@router.get("/groups", response_class=HTMLResponse)
async def list_groups(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    sd = sidebar_data(session, user.id)
    return templates.TemplateResponse(
        "groups/list.html",
        {"request": request, "user": user, **sd},
    )


@router.get("/groups/add", response_class=HTMLResponse)
async def add_form(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    sd = sidebar_data(session, user.id)
    return templates.TemplateResponse(
        "groups/form.html",
        {"request": request, "group": None, **sd, "user": user},
    )


@router.post("/groups/add")
async def add_group(
    name: str = Form(...),
    is_public: Optional[str] = Form(default=None),
    parent_id: Optional[int] = Form(default=None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    group = Group(
        user_id=user.id,
        name=name,
        is_public=is_public is not None,
        parent_id=_validate_parent(session, parent_id, user.id),
    )
    session.add(group)
    session.commit()
    return RedirectResponse(url="/groups", status_code=303)


@router.get("/groups/{group_id}/edit", response_class=HTMLResponse)
async def edit_form(
    request: Request,
    group_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    group = session.get(Group, group_id)
    if not group or group.user_id != user.id:
        raise HTTPException(status_code=404)
    sd = sidebar_data(session, user.id)
    return templates.TemplateResponse(
        "groups/form.html",
        {"request": request, "group": group, **sd, "user": user},
    )


@router.post("/groups/{group_id}/edit")
async def edit_group(
    group_id: int,
    name: str = Form(...),
    is_public: Optional[str] = Form(default=None),
    parent_id: Optional[int] = Form(default=None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    group = session.get(Group, group_id)
    if not group or group.user_id != user.id:
        raise HTTPException(status_code=404)
    group.name = name
    group.is_public = is_public is not None
    group.parent_id = _validate_parent(session, parent_id, user.id, exclude_id=group_id)
    session.add(group)
    session.commit()
    return RedirectResponse(url="/groups", status_code=303)


@router.post("/groups/{group_id}/delete")
async def delete_group(
    group_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    group = session.get(Group, group_id)
    if not group or group.user_id != user.id:
        raise HTTPException(status_code=404)
    session.execute(
        text("UPDATE groups SET parent_id = NULL WHERE parent_id = :id AND user_id = :uid"),
        {"id": group_id, "uid": user.id},
    )
    session.execute(text("DELETE FROM link_groups WHERE group_id = :id"), {"id": group_id})
    session.delete(group)
    session.commit()
    return RedirectResponse(url="/groups", status_code=303)
