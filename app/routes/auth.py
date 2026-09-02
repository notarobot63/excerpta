from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..csrf import csrf_protect
from ..templates_cfg import templates

router = APIRouter()


@router.post("/auth/logout", dependencies=[Depends(csrf_protect)])
async def logout(request: Request):
    """Déconnexion en POST, protégée par CSRF.

    En GET, n'importe quelle page tierce déconnectait le visiteur avec une
    simple balise `img`. La nuisance est modeste, mais le raisonnement qui
    justifie le GET pour le sélecteur de langue (action d'affichage, idempotente,
    utile sans session) ne s'applique pas ici.
    """
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)


@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    safe_error = error if error in ("1", "inactive") else None
    return templates.TemplateResponse(
        request, "auth/login.html", {"error": safe_error}
    )
