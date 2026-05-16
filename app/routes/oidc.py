from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlmodel import Session, select

from ..config import settings
from ..database import get_session
from ..models import User

router = APIRouter()

_oauth: OAuth | None = None


def _get_client() -> OAuth:
    global _oauth
    if _oauth is None:
        _oauth = OAuth()
        _oauth.register(
            name="pocketid",
            server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            client_kwargs={
                "scope": "openid email profile",
                "code_challenge_method": "S256",
            },
        )
    return _oauth


def _maybe_promote_admin(session: Session, user: User, is_new: bool) -> None:
    if settings.admin_email:
        if user.email == settings.admin_email and not user.is_admin:
            user.is_admin = True
    elif is_new:
        count = session.execute(text("SELECT COUNT(*) FROM users")).scalar()
        if count == 1:
            user.is_admin = True


@router.get("/auth/oidc/start")
async def oidc_start(request: Request):
    if not settings.oidc_client_id:
        return RedirectResponse(url="/auth/dev-login", status_code=303)
    redirect_uri = f"{settings.base_url}/auth/oidc/callback"
    return await _get_client().pocketid.authorize_redirect(request, redirect_uri)


@router.get("/auth/oidc/callback")
async def oidc_callback(request: Request, session: Session = Depends(get_session)):
    try:
        token = await _get_client().pocketid.authorize_access_token(request)
    except Exception:
        return RedirectResponse(url="/auth/login?error=1", status_code=303)

    userinfo = token.get("userinfo")
    if not userinfo:
        try:
            userinfo = await _get_client().pocketid.userinfo(token=token)
        except Exception:
            return RedirectResponse(url="/auth/login?error=1", status_code=303)

    sub = userinfo.get("sub")
    if not sub:
        return RedirectResponse(url="/auth/login?error=1", status_code=303)

    existing = session.exec(select(User).where(User.oidc_sub == sub)).first()
    is_new = existing is None

    if is_new:
        user = User(
            oidc_sub=sub,
            email=userinfo.get("email", ""),
            name=userinfo.get("name", "") or userinfo.get("preferred_username", ""),
        )
        session.add(user)
        session.flush()
    else:
        user = existing
        user.email = userinfo.get("email", user.email)
        user.name = userinfo.get("name", "") or userinfo.get("preferred_username", "") or user.name
        session.add(user)

    _maybe_promote_admin(session, user, is_new)

    session.commit()
    session.refresh(user)

    if not user.is_active:
        return RedirectResponse(url="/auth/login?error=inactive", status_code=303)

    request.session["user_id"] = user.id
    return RedirectResponse(url="/", status_code=303)
