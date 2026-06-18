from collections import defaultdict
from typing import List, Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, select

from ..auth import get_current_user
from ..database import get_session
from ..models import Folder, User
from ..templates_cfg import templates
from ..utils import build_folder_tree, folder_alpha_key, sidebar_data

router = APIRouter()


class RenameFolderBody(BaseModel):
    name: str


def _validate_parent(session: Session, parent_id: Optional[int], user_id: int, exclude_id: Optional[int] = None) -> Optional[int]:
    if not parent_id:
        return None
    if parent_id == exclude_id:
        return None
    parent = session.get(Folder, parent_id)
    if not parent or parent.user_id != user_id:
        return None
    return parent_id


@router.get("/folders", response_class=HTMLResponse)
async def list_folders(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    sd = sidebar_data(session, user.id)
    return templates.TemplateResponse(
        "folders/list.html",
        {"request": request, "user": user, **sd},
    )


@router.get("/folders/add", response_class=HTMLResponse)
async def add_form(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    sd = sidebar_data(session, user.id)
    return templates.TemplateResponse(
        "folders/form.html",
        {"request": request, "folder": None, **sd, "user": user},
    )


@router.post("/folders/add")
async def add_folder(
    name: str = Form(...),
    is_public: Optional[str] = Form(default=None),
    parent_id: Optional[str] = Form(default=None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    pid = int(parent_id) if parent_id and parent_id.strip().isdigit() else None
    # sort_order = dernier parmi les frères
    siblings = list(session.exec(
        select(Folder).where(Folder.user_id == user.id, Folder.parent_id == pid)
    ).all())
    next_order = max((f.sort_order for f in siblings), default=-1) + 1
    folder = Folder(
        user_id=user.id,
        name=name,
        is_public=is_public is not None,
        parent_id=_validate_parent(session, pid, user.id),
        sort_order=next_order,
    )
    session.add(folder)
    session.commit()
    return RedirectResponse(url="/folders", status_code=303)


@router.get("/folders/{folder_id}/edit", response_class=HTMLResponse)
async def edit_form(
    request: Request,
    folder_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    folder = session.get(Folder, folder_id)
    if not folder or folder.user_id != user.id:
        raise HTTPException(status_code=404)
    sd = sidebar_data(session, user.id)
    return templates.TemplateResponse(
        "folders/form.html",
        {"request": request, "folder": folder, **sd, "user": user},
    )


@router.post("/folders/{folder_id}/edit")
async def edit_folder(
    folder_id: int,
    name: str = Form(...),
    is_public: Optional[str] = Form(default=None),
    parent_id: Optional[str] = Form(default=None),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    folder = session.get(Folder, folder_id)
    if not folder or folder.user_id != user.id:
        raise HTTPException(status_code=404)
    pid = int(parent_id) if parent_id and parent_id.strip().isdigit() else None
    folder.name = name
    folder.is_public = is_public is not None
    folder.parent_id = _validate_parent(session, pid, user.id, exclude_id=folder_id)
    session.add(folder)
    session.commit()
    return RedirectResponse(url="/folders", status_code=303)


@router.post("/folders/{folder_id}/rename")
async def rename_folder(
    folder_id: int,
    body: RenameFolderBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    folder = session.get(Folder, folder_id)
    if not folder or folder.user_id != user.id:
        raise HTTPException(status_code=404)
    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="Nom vide")
    folder.name = new_name
    session.add(folder)
    session.commit()
    return JSONResponse({"ok": True, "new_name": new_name})


@router.post("/folders/sort-alpha")
async def sort_folders_alpha(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Réordonne les dossiers A→Z par groupe de frères (action ponctuelle)."""
    folders = list(session.exec(select(Folder).where(Folder.user_id == user.id)).all())
    groups: dict = defaultdict(list)
    for f in folders:
        groups[f.parent_id].append(f)
    for siblings in groups.values():
        siblings.sort(key=lambda f: folder_alpha_key(f.name))
        for i, f in enumerate(siblings):
            f.sort_order = i
            session.add(f)
    session.commit()
    return JSONResponse({"ok": True})


@router.post("/folders/{folder_id}/delete")
async def delete_folder(
    folder_id: int,
    delete_links: str = Form("0"),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    folder = session.get(Folder, folder_id)
    if not folder or folder.user_id != user.id:
        raise HTTPException(status_code=404)
    if delete_links == "1":
        session.execute(text("DELETE FROM links WHERE folder_id = :id AND user_id = :uid"),
                        {"id": folder_id, "uid": user.id})
    else:
        session.execute(text("UPDATE links SET folder_id = NULL WHERE folder_id = :id"),
                        {"id": folder_id})
    # Les sous-dossiers remontent à la racine
    session.execute(text("UPDATE folders SET parent_id = NULL WHERE parent_id = :id"),
                    {"id": folder_id})
    session.delete(folder)
    session.commit()
    return RedirectResponse(url="/folders", status_code=303)


@router.post("/folders/reorder")
async def reorder_folders(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Reçoit [{id, parent_id, sort_order}, ...] et met à jour la position et le parent."""
    body = await request.json()
    if not isinstance(body, list):
        raise HTTPException(status_code=400)
    for item in body:
        fid = item.get("id")
        new_parent = item.get("parent_id")  # None ou int
        new_order = item.get("sort_order", 0)
        folder = session.get(Folder, fid)
        if not folder or folder.user_id != user.id:
            continue
        # Valider le parent si fourni
        if new_parent is not None:
            parent = session.get(Folder, new_parent)
            if not parent or parent.user_id != user.id or new_parent == fid:
                new_parent = None
        folder.parent_id = new_parent
        folder.sort_order = new_order
        session.add(folder)
    session.commit()
    return JSONResponse({"ok": True})
