"""Choix de la langue d'interface.

Volontairement en GET, contrairement au reste des changements d'état du projet
qui passent par POST + CSRF. Trois raisons :

1. L'action est bénigne et idempotente : elle ne change qu'une préférence
   d'affichage, jamais une donnée.
2. Un POST protégé par CSRF imposerait `csrf_input()` sur la page de connexion
   et sur les pages publiques, donc un cookie de session à tout visiteur
   anonyme, pour un simple sélecteur de langue.
3. Un lien fonctionne sans JavaScript partout, y compris sur les pages
   autonomes qui ne chargent pas `app.js`.

Le risque résiduel est qu'un tiers fasse changer la langue d'affichage d'un
visiteur. La redirection est en revanche strictement validée : un `next`
non interne est ignoré, sans quoi la route serait un redirecteur ouvert.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from .. import i18n
from ..database import get_session
from ..models import User
from ..utils import safe_next

router = APIRouter()

# Un an : la préférence d'un visiteur anonyme doit survivre à sa session.
_COOKIE_MAX_AGE = 365 * 24 * 3600


@router.get("/lang/{code}")
async def set_language(
    code: str,
    request: Request,
    session: Session = Depends(get_session),
):
    target = safe_next(request.query_params.get("next"))

    # Une locale inconnue ne doit pas pouvoir être stockée : elle rendrait la
    # préférence inopérante et polluerait la base.
    chosen = i18n.negotiate([code], i18n.available_locales())
    if not chosen:
        return RedirectResponse(url=target, status_code=303)

    response = RedirectResponse(url=target, status_code=303)
    # Le cookie sert aux pages sans utilisateur (connexion, pages publiques)
    # et reste posé pour les autres : il évite une lecture en base à chaque
    # requête, la session étant vidée à la déconnexion.
    response.set_cookie(
        i18n.LOCALE_COOKIE,
        chosen,
        max_age=_COOKIE_MAX_AGE,
        httponly=False,  # aucune donnée sensible, et lisible par le thème JS
        samesite="lax",
        secure=request.url.scheme == "https",
    )

    user_id = request.session.get("user_id")
    if user_id:
        user = session.get(User, user_id)
        if user:
            user.language = chosen
            session.add(user)
            session.commit()
        request.session["lang"] = chosen

    return response
