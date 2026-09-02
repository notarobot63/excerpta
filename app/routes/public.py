from datetime import timezone
from email.utils import format_datetime
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import or_, text
from sqlmodel import Session, select

from ..config import settings
from ..database import get_session
from ..models import Link, LinkTagLink, Tag, User
from ..ratelimit import rate_limit
from ..templates_cfg import templates

router = APIRouter()

# Plafond de liens rendus sur une page publique (anti-DoS sur route anonyme).
_PUBLIC_MAX_LINKS = 500


def _get_public_owner(session: Session, slug: str) -> User:
    owner = session.exec(
        select(User).where(User.public_slug == slug, User.is_active == True)
    ).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Public page not found")
    return owner


@router.get("/u/{slug}/feed.xml", dependencies=[Depends(rate_limit(60, 60))])
async def public_feed(
    slug: str,
    request: Request,
    session: Session = Depends(get_session),
):
    owner = _get_public_owner(session, slug)
    links = list(
        session.exec(
            select(Link)
            .where(Link.user_id == owner.id, Link.is_public == True)
            .order_by(Link.created_at.desc())
            .limit(100)
        ).all()
    )
    page_title = owner.public_page_title or "Liens publics"
    base = settings.base_url.rstrip("/")
    feed_link = f"{base}/u/{owner.public_slug}"
    parts = []
    for lk in links:
        # %a/%b suivent la locale du processus : sous une locale fr_FR le flux
        # sortirait « sam., 26 juil. » et ne serait plus du RFC-822 valide.
        pub = format_datetime(lk.created_at.replace(tzinfo=timezone.utc))
        parts.append(
            f"<item>"
            f"<title>{xml_escape(lk.title or lk.url)}</title>"
            f"<link>{xml_escape(lk.url)}</link>"
            f"<description>{xml_escape(lk.description or '')}</description>"
            f"<pubDate>{pub}</pubDate>"
            f"<guid>{xml_escape(lk.url)}</guid>"
            f"</item>"
        )
    items = "\n".join(parts)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        f"<title>{xml_escape(page_title)} - Excerpta</title>"
        f"<link>{xml_escape(feed_link)}</link>"
        f"<description>{xml_escape(page_title)} partagés via Excerpta</description>"
        f"{items}"
        "</channel></rss>"
    )
    return Response(content=xml, media_type="application/rss+xml")


@router.get("/u/{slug}", response_class=HTMLResponse,
            dependencies=[Depends(rate_limit(60, 60))])
async def public_links(
    slug: str,
    request: Request,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    session: Session = Depends(get_session),
):
    owner = _get_public_owner(session, slug)
    page_title = owner.public_page_title or "Liens publics"

    # Filtrage en base plutôt qu'en Python : cette route est la seule surface
    # non authentifiée, charger toute la collection à chaque requête en faisait
    # un levier de déni de service.
    stmt = select(Link).where(Link.user_id == owner.id, Link.is_public == True)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Link.title.ilike(pattern),
                Link.description.ilike(pattern),
                Link.note.ilike(pattern),
                Link.url.ilike(pattern),
            )
        )
    if tag:
        # Filtrage en base et non après coup : appliqué en Python sur la page
        # déjà tronquée à _PUBLIC_MAX_LINKS, un tag ne figurant qu'au-delà de
        # cette limite ne renvoyait aucun résultat.
        stmt = (
            stmt.join(LinkTagLink, LinkTagLink.link_id == Link.id)
            .join(Tag, Tag.id == LinkTagLink.tag_id)
            .where(Tag.user_id == owner.id, Tag.name == tag)
        )

    links = list(
        session.exec(
            stmt.order_by(Link.created_at.desc()).limit(_PUBLIC_MAX_LINKS)
        ).all()
    )

    # Sélecteur bâti sur l'ensemble des liens publics, pas sur la page affichée :
    # le construire depuis `links` le réduisait aux seuls tags des résultats
    # courants, si bien qu'un filtre actif empêchait de passer à un autre tag.
    all_tags = [
        row[0]
        for row in session.execute(
            text(
                "SELECT DISTINCT t.name FROM tags t"
                " JOIN link_tags lt ON lt.tag_id = t.id"
                " JOIN links l ON l.id = lt.link_id"
                " WHERE l.user_id = :uid AND l.is_public = 1"
                " ORDER BY t.name"
            ),
            {"uid": owner.id},
        ).fetchall()
    ]

    return templates.TemplateResponse(
        request,
        "public/index.html",
        {
            "links": links,
            "all_tags": all_tags,
            "current_tag": tag,
            "q": q or "",
            "total": len(links),
            "page_title": page_title,
            "base_path": f"/u/{owner.public_slug}",
        },
    )
