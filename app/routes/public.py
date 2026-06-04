from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlmodel import Session, select

from ..config import settings
from ..database import get_session
from ..models import Link
from ..templates_cfg import templates

router = APIRouter()


@router.get("/public/feed.xml")
async def public_feed(
    request: Request,
    session: Session = Depends(get_session),
):
    links = list(
        session.exec(
            select(Link).where(Link.is_public == True).order_by(Link.created_at.desc()).limit(100)
        ).all()
    )
    base = settings.base_url.rstrip("/")
    items = ""
    for lk in links:
        pub = lk.created_at.strftime("%a, %d %b %Y %H:%M:%S +0000")
        desc = xml_escape(lk.description or "")
        items += (
            f"<item>"
            f"<title>{xml_escape(lk.title or lk.url)}</title>"
            f"<link>{xml_escape(lk.url)}</link>"
            f"<description>{desc}</description>"
            f"<pubDate>{pub}</pubDate>"
            f"<guid>{xml_escape(lk.url)}</guid>"
            f"</item>\n"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        f"<title>Liens publics - Excerpta</title>"
        f"<link>{xml_escape(base)}/public</link>"
        f"<description>Liens publics partagés via Excerpta</description>"
        f"{items}"
        "</channel></rss>"
    )
    return Response(content=xml, media_type="application/rss+xml")


@router.get("/public", response_class=HTMLResponse)
async def public_links(
    request: Request,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    session: Session = Depends(get_session),
):
    links = list(
        session.exec(
            select(Link).where(Link.is_public == True).order_by(Link.created_at.desc())
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
        },
    )
