import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request

from ..crypto import encrypt, hmac_key
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlmodel import Session

from ..auth import get_admin_user
from ..config import settings as cfg
from ..database import get_session
from ..models import User
from ..ratelimit import rate_limit
from ..templates_cfg import templates
from ..utils import sidebar_data

_log = logging.getLogger("excerpta.admin")

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

_admin_post_rl = [Depends(rate_limit(30, 3600))]


@router.post("/users/{uid}/toggle-active", dependencies=_admin_post_rl)
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
    target.session_version += 1  # invalide les sessions actives
    session.add(target)
    session.commit()
    _log.info("admin#%s toggled active for user#%s → %s", admin.id, uid, target.is_active)
    return RedirectResponse(url=f"/admin/users/{uid}", status_code=303)


@router.post("/users/{uid}/toggle-admin", dependencies=_admin_post_rl)
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
    target.session_version += 1  # invalide les sessions actives
    session.add(target)
    session.commit()
    _log.info("admin#%s toggled admin for user#%s → %s", admin.id, uid, target.is_admin)
    return RedirectResponse(url=f"/admin/users/{uid}", status_code=303)


@router.post("/users/{uid}/regen-key", dependencies=_admin_post_rl)
async def regen_key(
    uid: int,
    admin: User = Depends(get_admin_user),
    session: Session = Depends(get_session),
):
    target = session.get(User, uid)
    if not target:
        raise HTTPException(status_code=404)
    new_key = secrets.token_urlsafe(32)
    target.api_key = encrypt(new_key)
    target.api_key_hmac = hmac_key(new_key)
    session.add(target)
    session.commit()
    _log.info("admin#%s regenerated API key for user#%s", admin.id, uid)
    return RedirectResponse(url=f"/admin/users/{uid}", status_code=303)


@router.post("/users/{uid}/delete", dependencies=_admin_post_rl)
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
    target_name = target.name
    # Cascade manuelle dans l'ordre des dépendances FK
    session.execute(text("DELETE FROM fts_links   WHERE link_id IN (SELECT id FROM links WHERE user_id=:id)"), {"id": uid})
    session.execute(text("DELETE FROM link_tags   WHERE link_id IN (SELECT id FROM links WHERE user_id=:id)"), {"id": uid})
    session.execute(text("DELETE FROM link_groups WHERE link_id IN (SELECT id FROM links WHERE user_id=:id)"), {"id": uid})
    session.execute(text("DELETE FROM links       WHERE user_id=:id"), {"id": uid})
    session.execute(text("DELETE FROM tags        WHERE user_id=:id"), {"id": uid})
    session.execute(text("DELETE FROM groups      WHERE user_id=:id"), {"id": uid})
    session.execute(text("DELETE FROM users       WHERE id=:id"), {"id": uid})
    session.commit()
    _log.warning("admin#%s deleted user#%s (%s) and all their data", admin.id, uid, target_name)
    return RedirectResponse(url="/admin/users", status_code=303)
