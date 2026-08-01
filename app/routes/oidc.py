from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlmodel import Session, select
from threading import Lock

from ..config import settings
from ..crypto import encrypt, hmac_key
from ..database import get_session
from ..models import User
from ..ratelimit import rate_limit
from ..utils import unique_public_slug

router = APIRouter()

_oauth: OAuth | None = None
_first_user_lock = Lock()


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


def _maybe_promote_admin(
    session: Session, user: User, is_new: bool, email_verified: bool = False
) -> None:
    if settings.admin_email:
        # admin_email configured: promote only if email matches exactly.
        # Sans preuve de vérification par l'IdP, n'importe qui pouvant se
        # déclarer cet email obtiendrait l'admin.
        if not email_verified and settings.require_verified_email:
            return
        if user.email and user.email == settings.admin_email and not user.is_admin:
            user.is_admin = True
    elif is_new:
        # No admin_email: first user ever gets admin (COUNT includes the flushed user)
        count = session.execute(text("SELECT COUNT(*) FROM users")).scalar()
        if count == 1:
            user.is_admin = True


@router.get("/auth/oidc/start", dependencies=[Depends(rate_limit(10, 60))])
async def oidc_start(request: Request):
    redirect_uri = f"{settings.base_url}/auth/oidc/callback"
    return await _get_client().pocketid.authorize_redirect(request, redirect_uri)


@router.get("/auth/oidc/callback", dependencies=[Depends(rate_limit(10, 60))])
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
    # OIDC core : email_verified est un booléen, mais certains IdP le
    # sérialisent en chaîne. On n'accepte que le vrai booléen ou "true".
    _ev = userinfo.get("email_verified")
    email_verified = _ev is True or (isinstance(_ev, str) and _ev.lower() == "true")
    if not sub:
        return RedirectResponse(url="/auth/login?error=1", status_code=303)

    existing = session.exec(select(User).where(User.oidc_sub == sub)).first()
    is_new = existing is None

    if is_new:
        with _first_user_lock:
            # Re-check under lock to avoid race on first-user admin promotion
            existing2 = session.exec(select(User).where(User.oidc_sub == sub)).first()
            if existing2:
                user = existing2
                is_new = False
            else:
                import secrets as _sec
                raw_key = _sec.token_urlsafe(32)
                user = User(
                    oidc_sub=sub,
                    email=userinfo.get("email", ""),
                    name=userinfo.get("name", "") or userinfo.get("preferred_username", ""),
                    api_key=encrypt(raw_key),
                    api_key_hmac=hmac_key(raw_key),
                )
                session.add(user)
                session.flush()
                user.public_slug = unique_public_slug(session, user.name, user.id)
                _maybe_promote_admin(session, user, is_new=True, email_verified=email_verified)
                session.commit()
                session.refresh(user)
    else:
        user = existing

    if not is_new:
        user.email = userinfo.get("email", user.email)
        user.name = userinfo.get("name", "") or userinfo.get("preferred_username", "") or user.name
        _maybe_promote_admin(session, user, is_new=False, email_verified=email_verified)
        session.add(user)
        session.commit()
        session.refresh(user)

    if not user.is_active:
        return RedirectResponse(url="/auth/login?error=inactive", status_code=303)

    # Rotation de session : tout ce qui précédait l'authentification (dont le
    # csrf_token) est jeté, sinon un cookie de session fourni par un tiers
    # resterait valide et son CSRF token connu après le login.
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["session_version"] = user.session_version

    # Langue : la session est vidée juste au-dessus, il faut donc y reporter la
    # préférence enregistrée, sinon elle serait ignorée jusqu'à la prochaine
    # visite. Et si le visiteur a choisi une langue AVANT de se connecter, on
    # l'adopte comme préférence : sans cela, son choix serait perdu au login.
    if user.language:
        request.session["lang"] = user.language
    else:
        from ..i18n import LOCALE_COOKIE, available_locales, negotiate

        chosen = negotiate([request.cookies.get(LOCALE_COOKIE) or ""], available_locales())
        if chosen:
            user.language = chosen
            session.add(user)
            session.commit()
            request.session["lang"] = chosen

    return RedirectResponse(url="/", status_code=303)
