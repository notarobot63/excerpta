from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from ..config import settings
from ..database import get_session
from ..models import User
from ..templates_cfg import templates

router = APIRouter()


@router.get("/auth/dev-login")
async def dev_login(request: Request, session: Session = Depends(get_session)):
    """Connexion de développement - désactivé quand OIDC est configuré."""
    if settings.oidc_client_id:
        raise HTTPException(status_code=403, detail="OIDC est configuré")
    user = session.exec(select(User).where(User.oidc_sub == "dev")).first()
    if not user:
        user = User(oidc_sub="dev", email="dev@local", name="Dev User")
        session.add(user)
        session.commit()
        session.refresh(user)
    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)


@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    if not settings.oidc_client_id:
        return RedirectResponse(url="/auth/dev-login", status_code=303)
    safe_error = error if error in ("1", "inactive") else None
    return templates.TemplateResponse(
        "auth/login.html", {"request": request, "error": safe_error}
    )
