import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlmodel import Session

from ..auth import get_admin_user
from ..config import settings as cfg
from ..database import get_session
from ..models import User
from ..templates_cfg import templates
from ..utils import sidebar_data

router = APIRouter(prefix="/admin")


def _bookmarklet(base_url: str) -> str:
    return (
        "javascript:(function(){"
        f"window.open('{base_url}/links/add?url='+encodeURIComponent(window.location.href)"
        "+'&title='+encodeURIComponent(document.title),'_blank')"
        "})();"
    )


def _db_size_mb(session: Session) -> str:
    try:
        pages = session.execute(text("PRAGMA page_count")).scalar()
        size = session.execute(text("PRAGMA page_size")).scalar()
        mb = (pages * size) / (1024 * 1024)
        return f"{mb:.1f} Mo"
    except Exception:
        return "?"


# ── Dashboard ─────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    stats = {
        "users": session.execute(text("SELECT COUNT(*) FROM users")).scalar(),
        "links": session.execute(text("SELECT COUNT(*) FROM links")).scalar(),
        "tags":  session.execute(text("SELECT COUNT(*) FROM tags")).scalar(),
        "groups": session.execute(text("SELECT COUNT(*) FROM groups")).scalar(),
        "db_size": _db_size_mb(session),
        "last_link": session.execute(
            text("SELECT MAX(created_at) FROM links")
        ).scalar(),
    }
    recent = session.execute(
        text("""
            SELECT l.title, l.url, u.name, l.created_at
            FROM links l JOIN users u ON u.id = l.user_id
            ORDER BY l.created_at DESC LIMIT 10
        """)
    ).fetchall()
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request, "user": admin,
        "stats": stats, "recent": recent,
        **sidebar_data(session, admin.id),
    })


# ── Users list ────────────────────────────────────────────────────────────────

@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    rows = session.execute(text("""
        SELECT u.id, u.name, u.email, u.is_admin, u.is_active, u.created_at,
               COUNT(DISTINCT l.id) AS link_count,
               COUNT(DISTINCT t.id) AS tag_count,
               MAX(l.created_at)    AS last_link_at
        FROM users u
        LEFT JOIN links l ON l.user_id = u.id
        LEFT JOIN tags  t ON t.user_id = u.id
        GROUP BY u.id
        ORDER BY u.created_at
    """)).fetchall()
    return templates.TemplateResponse("admin/users.html", {
        "request": request, "user": admin, "rows": rows,
        **sidebar_data(session, admin.id),
    })


# ── User detail ───────────────────────────────────────────────────────────────

@router.get("/users/{uid}", response_class=HTMLResponse)
async def user_detail(
    request: Request,
    uid: int,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    target = session.get(User, uid)
    if not target:
        raise HTTPException(status_code=404)
    stats = {
        "links":  session.execute(text("SELECT COUNT(*) FROM links  WHERE user_id=:id"), {"id": uid}).scalar(),
        "tags":   session.execute(text("SELECT COUNT(*) FROM tags   WHERE user_id=:id"), {"id": uid}).scalar(),
        "groups": session.execute(text("SELECT COUNT(*) FROM groups WHERE user_id=:id"), {"id": uid}).scalar(),
    }
    recent_links = session.execute(
        text("SELECT title, url, created_at FROM links WHERE user_id=:id ORDER BY created_at DESC LIMIT 5"),
        {"id": uid},
    ).fetchall()
    bm = _bookmarklet(cfg.base_url)
    admin_count = session.execute(text("SELECT COUNT(*) FROM users WHERE is_admin=1")).scalar()
    return templates.TemplateResponse("admin/user_detail.html", {
        "request": request, "user": admin,
        "target": target, "stats": stats,
        "recent_links": recent_links,
        "bookmarklet": bm,
        "admin_count": admin_count,
        **sidebar_data(session, admin.id),
    })


# ── Actions ───────────────────────────────────────────────────────────────────

@router.post("/users/{uid}/toggle-active")
async def toggle_active(
    uid: int,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    target = session.get(User, uid)
    if not target:
        raise HTTPException(status_code=404)
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Impossible de se désactiver soi-même")
    target.is_active = not target.is_active
    session.add(target)
    session.commit()
    return RedirectResponse(url=f"/admin/users/{uid}", status_code=303)


@router.post("/users/{uid}/toggle-admin")
async def toggle_admin(
    uid: int,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    target = session.get(User, uid)
    if not target:
        raise HTTPException(status_code=404)
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Impossible de se retirer les droits admin")
    admin_count = session.execute(text("SELECT COUNT(*) FROM users WHERE is_admin=1")).scalar()
    if target.is_admin and admin_count <= 1:
        raise HTTPException(status_code=400, detail="Il doit rester au moins un administrateur")
    target.is_admin = not target.is_admin
    session.add(target)
    session.commit()
    return RedirectResponse(url=f"/admin/users/{uid}", status_code=303)


@router.post("/users/{uid}/regen-key")
async def regen_key(
    uid: int,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    target = session.get(User, uid)
    if not target:
        raise HTTPException(status_code=404)
    target.api_key = secrets.token_urlsafe(32)
    session.add(target)
    session.commit()
    return RedirectResponse(url=f"/admin/users/{uid}", status_code=303)


@router.post("/users/{uid}/delete")
async def delete_user(
    uid: int,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    target = session.get(User, uid)
    if not target:
        raise HTTPException(status_code=404)
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Impossible de supprimer son propre compte")
    # Cascade manuelle
    session.execute(text("DELETE FROM fts_links   WHERE link_id IN (SELECT id FROM links WHERE user_id=:id)"), {"id": uid})
    session.execute(text("DELETE FROM link_tags   WHERE link_id IN (SELECT id FROM links WHERE user_id=:id)"), {"id": uid})
    session.execute(text("DELETE FROM link_groups WHERE link_id IN (SELECT id FROM links WHERE user_id=:id)"), {"id": uid})
    session.execute(text("DELETE FROM links  WHERE user_id=:id"), {"id": uid})
    session.execute(text("DELETE FROM link_tags   WHERE tag_id  IN (SELECT id FROM tags   WHERE user_id=:id)"), {"id": uid})
    session.execute(text("DELETE FROM tags   WHERE user_id=:id"), {"id": uid})
    session.execute(text("DELETE FROM link_groups WHERE group_id IN (SELECT id FROM groups WHERE user_id=:id)"), {"id": uid})
    session.execute(text("DELETE FROM groups WHERE user_id=:id"), {"id": uid})
    session.execute(text("DELETE FROM users  WHERE id=:id"), {"id": uid})
    session.commit()
    return RedirectResponse(url="/admin/users", status_code=303)
