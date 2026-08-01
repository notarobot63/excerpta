"""Scraping des métadonnées d'une URL (titre, description, favicon, vignette)
et mise à jour asynchrone d'un lien nouvellement créé."""
import logging
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from sqlmodel import Session, select

from ...database import engine as db_engine
from ...models import Link, LinkTagLink, Tag
from ...utils import refresh_link_fts
from .constants import MAX_DESC_LEN, MAX_TITLE_LEN
from .net_guard import _MAX_HTML_BYTES, _TooLarge, _assert_public_url, _read_limited, _safe_stream, _safe_url

logger = logging.getLogger("excerpta.links.enrichment")


async def _fetch_meta(url: str) -> dict:
    if not await _assert_public_url(url):
        return {"title": "", "description": "", "favicon_url": ""}
    try:
        async with _safe_stream("GET", url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3",
        }, timeout=5) as resp:
            if resp.status_code >= 400:
                return {"title": "", "description": "", "favicon_url": ""}
            final_url = str(resp.url)
            try:
                body = await _read_limited(resp, _MAX_HTML_BYTES)
            except _TooLarge:
                return {"title": "", "description": "", "favicon_url": ""}
        parsed = urlparse(final_url)
        soup = BeautifulSoup(body.decode(resp.encoding or "utf-8", errors="replace"), "html.parser")
        title = soup.find("title")
        desc = soup.find("meta", attrs={"property": "og:description"}) or soup.find(
            "meta", attrs={"name": "description"}
        )
        description_text = desc.get("content", "").strip() if desc else ""
        if not description_text:
            for p in soup.find_all("p"):
                text = p.get_text(" ", strip=True)
                if len(text) > 80:
                    description_text = text[:400]
                    break
        icon = soup.find("link", rel=lambda r: r and "icon" in r)
        favicon = ""
        if icon and icon.get("href"):
            href = icon["href"]
            if href.startswith("//"):
                href = f"{parsed.scheme}:{href}"
            elif not href.startswith("http"):
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            if _safe_url(href):  # reject protocol-relative & private IPs in favicon
                favicon = href
        if not favicon:
            candidate = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
            if _safe_url(candidate):
                favicon = candidate
        og_img = soup.find("meta", attrs={"property": "og:image"}) or \
                 soup.find("meta", attrs={"name": "twitter:image"})
        thumbnail = ""
        if og_img:
            raw = og_img.get("content", "").strip()
            if raw.startswith("//"):
                raw = f"{parsed.scheme}:{raw}"
            elif raw.startswith("/"):
                raw = f"{parsed.scheme}://{parsed.netloc}{raw}"
            if _safe_url(raw):
                thumbnail = raw
        if not thumbnail:
            content_zone = soup.find(["article", "main"]) or soup
            for img in content_zone.find_all("img"):
                raw = (
                    img.get("src", "")
                    or img.get("data-src", "")
                    or img.get("data-lazy-src", "")
                    or img.get("data-original", "")
                ).strip()
                if not raw or raw.startswith("data:"):
                    continue
                if raw.lower().endswith(".svg"):
                    continue
                try:
                    w = int(img.get("width") or 0)
                    h = int(img.get("height") or 0)
                    if (w and w < 100) or (h and h < 100):
                        continue
                except ValueError:
                    pass
                if raw.startswith("//"):
                    raw = f"{parsed.scheme}:{raw}"
                elif raw.startswith("/"):
                    raw = f"{parsed.scheme}://{parsed.netloc}{raw}"
                if raw.startswith("http") and _safe_url(raw):
                    thumbnail = raw
                    break
        return {
            "title": title.text.strip() if title else "",
            "description": description_text,
            "favicon_url": favicon,
            "thumbnail_url": thumbnail,
        }
    except Exception:
        logger.warning("Échec de récupération des métadonnées pour %s", url, exc_info=True)
        return {"title": "", "description": "", "favicon_url": ""}


async def _fetch_and_update_meta(link_id: int, url: str) -> None:
    """Récupère les métadonnées en arrière-plan et complète les champs vides du lien."""
    meta = await _fetch_meta(url)
    with Session(db_engine) as s:
        link = s.get(Link, link_id)
        if not link:
            return
        changed = False
        if (not link.title or link.title == url) and meta.get("title"):
            link.title = meta["title"][:MAX_TITLE_LEN]
            changed = True
        if not link.description and meta.get("description"):
            link.description = meta["description"][:MAX_DESC_LEN]
            changed = True
        if not link.favicon_url and meta.get("favicon_url"):
            link.favicon_url = meta["favicon_url"]
            changed = True
        if not link.thumbnail_url and meta.get("thumbnail_url"):
            link.thumbnail_url = meta["thumbnail_url"]
            changed = True
        if changed:
            s.add(link)
            s.flush()
            tags = list(s.exec(
                select(Tag).join(LinkTagLink, LinkTagLink.tag_id == Tag.id)
                .where(LinkTagLink.link_id == link.id)
            ).all())
            refresh_link_fts(s, link, tags)
            s.commit()
