from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, select

from ..auth import get_current_user
from ..database import get_session
from ..models import Tag, User
from ..templates_cfg import templates
from ..models import Link, LinkTagLink
from ..utils import refresh_link_fts, sidebar_data

router = APIRouter()


class RenameTagBody(BaseModel):
    name: str


def _reindex_links_of_tag(session: Session, tag_id: int, link_ids: list[int] | None = None) -> None:
    """Réécrit la ligne d'index des liens portant une étiquette.

    La colonne `tags` de l'index n'est recalculée par les déclencheurs qu'à
    l'écriture d'une ligne de `links` : renommer ou supprimer une étiquette ne
    touche pas cette table, il faut donc rafraîchir explicitement. Passer
    `link_ids` sert quand l'association a déjà été défaite et qu'on ne peut plus
    retrouver les liens concernés.
    """
    if link_ids is None:
        link_ids = [
            row[0]
            for row in session.execute(
                text("SELECT link_id FROM link_tags WHERE tag_id = :tid"), {"tid": tag_id}
            ).fetchall()
        ]
    session.flush()
    for link_id in link_ids:
        link = session.get(Link, link_id)
        if link:
            # `link.tags` a pu être chargée avant que les lignes de `link_tags`
            # ne soient retirées en SQL : sans expiration, l'ORM sert sa copie
            # en cache et on réécrit dans l'index une étiquette qui n'existe
            # plus. C'est ce qui laissait une étiquette supprimée trouvable par
            # la recherche.
            session.expire(link, ["tags"])
            refresh_link_fts(session, link, list(link.tags))


@router.get("/tags", response_class=HTMLResponse)
async def list_tags(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "tags/list.html",
        {"user": user, **sidebar_data(session, user.id)},
    )


@router.post("/tags/{tag_id}/rename")
async def rename_tag(
    tag_id: int,
    body: RenameTagBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    tag = session.get(Tag, tag_id)
    if not tag or tag.user_id != user.id:
        raise HTTPException(status_code=404)
    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="Empty name")
    existing = session.exec(
        select(Tag).where(Tag.user_id == user.id, Tag.name == new_name)
    ).first()
    if existing and existing.id != tag_id:
        # Liens qui n'ont pas encore le tag cible (évite les doublons)
        affected_ids = [
            row[0] for row in session.execute(
                text("SELECT link_id FROM link_tags WHERE tag_id = :old_id AND link_id NOT IN (SELECT link_id FROM link_tags WHERE tag_id = :new_id)"),
                {"old_id": tag_id, "new_id": existing.id},
            ).fetchall()
        ]
        session.execute(
            text("INSERT OR IGNORE INTO link_tags (link_id, tag_id) SELECT link_id, :new_id FROM link_tags WHERE tag_id = :old_id"),
            {"new_id": existing.id, "old_id": tag_id},
        )
        session.execute(text("DELETE FROM link_tags WHERE tag_id = :id"), {"id": tag_id})
        session.delete(tag)
        # Mettre à jour le FTS des liens réassignés
        _reindex_links_of_tag(session, existing.id, affected_ids)
        session.commit()
        return JSONResponse({"ok": True, "merged": True, "new_id": existing.id, "new_name": new_name})
    # Renommage simple : l'index porte encore l'ancien nom. Sans cette
    # réécriture, la recherche continuait de trouver le lien par l'étiquette
    # d'avant et l'ignorait sous la nouvelle.
    tag.name = new_name
    _reindex_links_of_tag(session, tag_id)
    session.commit()
    return JSONResponse({"ok": True, "merged": False, "new_id": tag_id, "new_name": new_name})


@router.post("/tags/{tag_id}/delete")
async def delete_tag(
    tag_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    tag = session.get(Tag, tag_id)
    if not tag or tag.user_id != user.id:
        raise HTTPException(status_code=404)
    # Liens portant ce tag : ils survivent à la suppression du tag, leur
    # entrée FTS doit être rafraîchie (colonne tags) et non supprimée.
    affected_ids = [
        row[0] for row in session.execute(
            text("SELECT link_id FROM link_tags WHERE tag_id = :tid"),
            {"tid": tag_id},
        ).fetchall()
    ]
    session.execute(text("DELETE FROM link_tags WHERE tag_id = :id"), {"id": tag_id})
    session.delete(tag)
    _reindex_links_of_tag(session, tag_id, affected_ids)
    session.commit()
    return RedirectResponse(url="/tags", status_code=303)
