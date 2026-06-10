from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlmodel import Session, select

from ..config import settings
from ..database import get_session
from ..models import Link, User
from ..templates_cfg import templates

router = APIRouter()


def _get_public_owner(session: Session, slug: str) -> User:
    owner = session.exec(
        select(User).where(User.public_slug == slug, User.is_active == True)
    ).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Page publique introuvable")
    return owner


@router.get("/u/{slug}/feed.xml")
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
        pub = lk.created_at.strftime("%a, %d %b %Y %H:%M:%S +0000")
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


@router.get("/u/{slug}", response_class=HTMLResponse)
async def public_links(
    slug: str,
    request: Request,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    session: Session = Depends(get_session),
):
    owner = _get_public_owner(session, slug)
    page_title = owner.public_page_title or "Liens publics"

    links = list(
        session.exec(
            select(Link)
            .where(Link.user_id == owner.id, Link.is_public == True)
            .order_by(Link.created_at.desc())
        ).all()
    )

    if q:
        q_lower = q.lower()
        links = [
            lk for lk in links
            if q_lower in (lk.title or "").lower()
            or q_lower in (lk.description or "").lower()
            or q_lower in (lk.note or "").lower()
            or q_lower in lk.url.lower()
        ]

    if tag:
        links = [lk for lk in links if any(t.name == tag for t in lk.tags)]

    all_tags: list[str] = []
    seen: set[str] = set()
    for lk in links:
        for t in lk.tags:
            if t.name not in seen:
                all_tags.append(t.name)
                seen.add(t.name)
    all_tags.sort()

    return templates.TemplateResponse(
        "public/index.html",
        {
            "request": request,
            "links": links,
            "all_tags": all_tags,
            "current_tag": tag,
            "q": q or "",
            "total": len(links),
            "page_title": page_title,
            "base_path": f"/u/{owner.public_slug}",
        },
    )
