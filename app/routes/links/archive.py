"""Archivage Wayback Machine (robuste, non silencieux)."""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from ...auth import get_current_user
from ...database import engine as db_engine, get_session
from ...demo import forbid_in_demo_dep
from ...models import Link, User
from ...ratelimit import rate_limit
from . import net_guard
from .net_guard import _safe_url

logger = logging.getLogger("excerpta.links.archive")

router = APIRouter()


async def _wayback_archive(link_id: int) -> None:
    """Archive l'URL d'un lien sur Wayback Machine et trace l'issue.

    Conçue pour tourner en tâche de fond (ouvre sa propre session). Met à jour
    archive_status = ok|failed (jamais d'échec silencieux) + archived_url/at.
    """
    with Session(db_engine) as db:
        link = db.get(Link, link_id)
        if not link:
            return
        url = link.url

    archived = None
    status = "failed"
    if _safe_url(url):
        try:
            resp = await net_guard._http_client.post(
                f"https://web.archive.org/save/{url}",
                headers={"User-Agent": "Excerpta/1.0 (+archivage personnel)"},
                follow_redirects=False,
                timeout=60,
            )
            if resp.status_code < 400:
                location = resp.headers.get("location", "") or resp.headers.get("content-location", "")
                if location:
                    archived = location if location.startswith("http") else f"https://web.archive.org{location}"
                else:
                    # Pas de redirection immédiate : lien vers toutes les captures
                    archived = f"https://web.archive.org/web/*/{url}"
                status = "ok"
            else:
                logger.warning("Wayback save HTTP %s pour le lien %s", resp.status_code, link_id)
        except Exception:
            logger.warning("Échec archivage Wayback pour le lien %s", link_id, exc_info=True)

    with Session(db_engine) as db:
        link = db.get(Link, link_id)
        if not link:
            return
        if status == "ok":
            link.archived_url = archived
            link.archived_at = datetime.now(timezone.utc).replace(tzinfo=None)
        link.archive_status = status
        db.add(link)
        db.commit()


# L'archivage soumet l'URL à un tiers public au nom de l'application : en démo
# l'adresse vient d'un inconnu, la route est donc fermée (la création de lien ne
# planifiait déjà aucun archivage, mais rien ne fermait ce déclenchement manuel).
@router.post("/links/{link_id}/archive",
             dependencies=[Depends(rate_limit(10, 60)), Depends(forbid_in_demo_dep)])
async def archive_link(
    link_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    link = session.get(Link, link_id)
    if not link or link.user_id != user.id:
        raise HTTPException(status_code=404)
    link.archive_status = "pending"
    session.add(link)
    session.commit()
    background_tasks.add_task(_wayback_archive, link.id)
    return RedirectResponse(url=f"/links/{link_id}/edit", status_code=303)


async def _archive_many(link_ids: list[int]) -> None:
    """Archivage en masse throttlé (Wayback rate-limit l'archivage anonyme)."""
    for lid in link_ids:
        await _wayback_archive(lid)
        await asyncio.sleep(6)
