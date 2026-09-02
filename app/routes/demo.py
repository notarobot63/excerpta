"""Routes du mode démo : page d'accueil et création d'un espace jetable.

L'espace n'est créé que sur POST explicite. Une création au premier GET
fabriquerait un utilisateur et son jeu de données à chaque passage de robot
d'indexation, ce qui remplirait la base sans qu'aucun visiteur humain n'en
profite.
"""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from ..auth import get_current_user
from ..config import settings
from ..database import get_session
from ..demo import CATALOG, add_catalog_link, create_demo_space
from ..models import Link, User
from ..ratelimit import rate_limit
from ..templates_cfg import templates
from ..utils import sidebar_data

router = APIRouter()


def _require_demo_mode() -> None:
    if not settings.demo_mode:
        raise HTTPException(status_code=404)


@router.get("/demo", response_class=HTMLResponse)
async def demo_home(request: Request):
    _require_demo_mode()
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "demo/index.html",
        {
            "app_name": settings.app_name,
            "demo_ttl_hours": settings.demo_ttl_hours,
        },
    )


@router.post("/demo/start", dependencies=[Depends(rate_limit(10, 3600))])
async def demo_start(request: Request, session: Session = Depends(get_session)):
    _require_demo_mode()
    user = create_demo_space(session)
    request.session["user_id"] = user.id
    request.session["session_version"] = user.session_version
    return RedirectResponse(url="/", status_code=303)


@router.get("/demo/catalogue", response_class=HTMLResponse)
async def demo_catalogue(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Catalogue de démarrage : ajout en un clic depuis une liste fermée.

    Vient en complément du formulaire d'ajout libre, ouvert aux visiteurs, et
    ne le remplace plus.
    """
    _require_demo_mode()
    deja = {
        link.url
        for link in session.exec(select(Link).where(Link.user_id == user.id)).all()
    }
    entrees = [
        {**entry, "deja_ajoute": entry["url"] in deja}
        for entry in CATALOG
    ]
    return templates.TemplateResponse(
        request,
        "demo/catalogue.html",
        {
            "user": user,
            "entrees": entrees,
            "restants": sum(1 for e in entrees if not e["deja_ajoute"]),
            **sidebar_data(session, user.id),
        },
    )


@router.post("/demo/add", dependencies=[Depends(rate_limit(60, 3600))])
async def demo_add(
    request: Request,
    url: str = Form(...),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    _require_demo_mode()
    add_catalog_link(session, user, url)  # refuse toute URL hors catalogue
    return RedirectResponse(url="/demo/catalogue", status_code=303)
