"""Vue lecteur : extraction de contenu lisible (Readability) + sanitisation (nh3)."""
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import nh3
from readability import Document
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from ...auth import get_current_user
from ...database import get_session
from ...models import Link, User
from ...ratelimit import rate_limit
from ...templates_cfg import templates
from .net_guard import _MAX_HTML_BYTES, _TooLarge, _assert_public_url, _read_limited, _safe_stream

router = APIRouter()

_READER_TAGS = {
    "p", "a", "b", "strong", "em", "i", "u", "s", "h1", "h2", "h3", "h4", "h5", "h6",
    "img", "figure", "figcaption", "ul", "ol", "li", "blockquote", "pre", "code",
    "br", "hr", "table", "thead", "tbody", "tr", "th", "td", "span", "div", "sup", "sub",
}
_READER_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title"}}


async def _extract_reader(url: str) -> Optional[dict]:
    """Récupère l'URL et extrait le contenu lisible (Readability) + sanitisation (nh3).

    Réutilise la garde SSRF et le plafond de taille du reste du module.
    Retourne {"title", "html"} ou None si échec/non extractible.
    """
    if not await _assert_public_url(url):
        return None
    try:
        async with _safe_stream("GET", url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3",
        }, timeout=10) as resp:
            if resp.status_code >= 400:
                return None
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype and not (ctype.startswith("text/html") or ctype.startswith("application/xhtml")):
                return None
            try:
                body = await _read_limited(resp, _MAX_HTML_BYTES)
            except _TooLarge:
                return None
            encoding = resp.encoding or "utf-8"
    except Exception:
        return None

    try:
        html = body.decode(encoding, errors="replace")
        doc = Document(html)
        title = (doc.short_title() or "").strip()
        summary = doc.summary(html_partial=True)
    except Exception:
        return None

    clean = nh3.clean(summary, tags=_READER_TAGS, attributes=_READER_ATTRS)
    if not clean or not clean.strip():
        return None
    return {"title": title, "html": clean}


@router.get("/links/{link_id}/read", response_class=HTMLResponse,
            dependencies=[Depends(rate_limit(30, 60))])
async def read_link(
    request: Request,
    link_id: int,
    refresh: int = 0,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    link = session.get(Link, link_id)
    if not link or link.user_id != user.id:
        raise HTTPException(status_code=404)

    # Les liens du catalogue de démo portent déjà un contenu lecteur préparé :
    # l'extraction ne se déclenche donc que pour les liens ajoutés par le
    # visiteur, derrière la même garde SSRF qu'en production.
    if refresh or not link.reader_html:
        data = await _extract_reader(link.url)
        if data and data["html"]:
            link.reader_title = (data["title"] or link.title or "")[:500]
            link.reader_html = data["html"]
            link.reader_failed = False
        else:
            link.reader_failed = True
        link.reader_extracted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(link)
        session.commit()
        session.refresh(link)

    reading_minutes = 0
    if link.reader_html:
        words = len(re.sub(r"<[^>]+>", " ", link.reader_html).split())
        reading_minutes = max(1, round(words / 200))

    return templates.TemplateResponse(
        request,
        "links/reader.html",
        {
            "user": user,
            "link": link,
            "reading_minutes": reading_minutes,
            "source_host": urlparse(link.url).netloc,
        },
    )
