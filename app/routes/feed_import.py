"""Import d'un flux RSS/Atom : chaque entrée devient un lien.

Pensé pour le flux privé des éléments enregistrés Reddit (le lien d'une
entrée y est la discussion), mais vaut pour tout flux. Contrairement à
FreshRSS, rien n'est renvoyé à la source : le dossier (racine) est retrouvé
par son nom, et un renommage en fait seulement créer un nouveau. Les URL déjà
vues sont mémorisées : un lien supprimé ou purgé ne revient pas.
"""
import asyncio
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from lxml import etree
from sqlmodel import Session, select

from ..auth import get_current_user
from ..background import spawn
from ..crypto import decrypt, encrypt
from ..database import engine, get_session
from ..models import FeedImportConfig, Folder, Link, User
from ..ratelimit import rate_limit
from ..templates_cfg import templates
from ..utils import sidebar_data
from .links import _assert_public_url, _UnsafeRedirect, refresh_links_meta, safe_request
from .links.constants import MAX_DESC_LEN, MAX_TITLE_LEN

logger = logging.getLogger("excerpta.feed_import")

settings_router = APIRouter()

_ATOM = "{http://www.w3.org/2005/Atom}"
# Reddit refuse les agents génériques (429) ; un nom d'application explicite passe.
_HEADERS = {"User-Agent": "Excerpta/1.0 (feed import)"}
_MAX_ENTRIES = 1000
_MAX_SEEN = 5000
_RSS1 = "{http://purl.org/rss/1.0/}"
_DC = "{http://purl.org/dc/elements/1.1/}"


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return _naive_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        pass
    try:
        return _naive_utc(parsedate_to_datetime(raw))
    except (TypeError, ValueError):
        return None


def _text(el) -> str:
    """Tout le texte d'un élément, enfants compris (contenu Atom de type xhtml)."""
    return "".join(el.itertext()).strip() if el is not None else ""


def _html_text(raw: str) -> str:
    if not raw:
        return ""
    return BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)[:MAX_DESC_LEN]


def parse_feed(body: bytes) -> list[dict]:
    """Entrées d'un flux Atom ou RSS 2.0 : url, title, description, published."""
    # Ni entités externes ni réseau : le flux vient d'une URL saisie par l'utilisateur.
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(body, parser)
    except etree.XMLSyntaxError as exc:
        raise RuntimeError(f"Invalid feed ({exc})")
    entries: list[dict] = []
    for entry in root.iter(f"{_ATOM}entry"):
        url = ""
        for link in entry.findall(f"{_ATOM}link"):
            if link.get("rel", "alternate") == "alternate" and link.get("href", "").startswith("http"):
                url = link.get("href")
                break
        entries.append({
            "url": url,
            "title": _text(entry.find(f"{_ATOM}title")),
            "description": _html_text(_text(entry.find(f"{_ATOM}content")) or _text(entry.find(f"{_ATOM}summary"))),
            "published": _parse_date(entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated")),
        })
    # RSS 2.0 (sans espace de noms) et RSS 1.0 / RDF
    for ns in ("", _RSS1):
        for item in root.iter(f"{ns}item"):
            entries.append({
                "url": _text(item.find(f"{ns}link")),
                "title": _text(item.find(f"{ns}title")),
                "description": _html_text(_text(item.find(f"{ns}description"))),
                "published": _parse_date(item.findtext("pubDate") or item.findtext(f"{_DC}date")),
            })
    return [e for e in entries if e["url"].startswith("http")][:_MAX_ENTRIES]


async def _fetch_feed(url: str) -> list[dict]:
    # Les messages de la garde SSRF citent l'URL, donc le jeton du flux : remplacés.
    try:
        resp = await safe_request("GET", url, headers=_HEADERS)
    except _UnsafeRedirect:
        raise RuntimeError("Feed URL refused (private address or unsafe redirect)")
    if resp.status_code != 200:
        raise RuntimeError(f"Feed returned HTTP {resp.status_code}")
    return parse_feed(resp.content)


_sync_locks: dict[int, asyncio.Lock] = {}


async def sync_feed(config: FeedImportConfig, session: Session) -> int:
    """Importe les nouvelles entrées du flux. Retourne le nombre de liens ajoutés."""
    lock = _sync_locks.setdefault(config.user_id, asyncio.Lock())
    async with lock:
        session.refresh(config)
        entries = await _fetch_feed(decrypt(config.feed_url))

        folder = session.exec(
            select(Folder).where(Folder.user_id == config.user_id, Folder.name == config.group_name,
                                 Folder.parent_id == None)  # noqa: E711
        ).first()
        if not folder:
            folder = Folder(user_id=config.user_id, name=config.group_name)
            session.add(folder)
            session.flush()

        seen = config.seen_urls.split("\n") if config.seen_urls else []
        urls = [e["url"] for e in entries]
        existing = set(seen)
        existing |= set(session.exec(
            select(Link.url).where(Link.user_id == config.user_id, Link.url.in_(urls))
        ).all()) if urls else set()

        new_links: list[Link] = []
        for e in entries:
            if e["url"] in existing:
                continue
            created = e["published"]
            link = Link(
                user_id=config.user_id,
                url=e["url"],
                title=(e["title"] or e["url"])[:MAX_TITLE_LEN],
                description=e["description"],
                folder_id=folder.id,
                **({"created_at": created, "updated_at": created} if created else {}),
            )
            session.add(link)
            new_links.append(link)
            existing.add(e["url"])
        if new_links:
            session.flush()

        # Le plus récent d'abord, borné : le flux ne rend que ses dernières entrées.
        config.seen_urls = "\n".join(list(dict.fromkeys(urls + seen))[:_MAX_SEEN])
        config.last_sync = datetime.now(timezone.utc).replace(tzinfo=None)
        config.synced_count += len(new_links)
        session.add(config)
        session.commit()

        if new_links:
            spawn(refresh_links_meta([(l.id, l.url) for l in new_links]), name="feed-import-refresh")
        return len(new_links)


async def sync_all_feeds() -> None:
    with Session(engine) as session:
        ids = list(session.exec(
            select(FeedImportConfig.id)
            .join(User, User.id == FeedImportConfig.user_id)
            .where(FeedImportConfig.is_enabled == True, User.is_active == True)  # noqa: E712
        ).all())
    for cid in ids:
        try:
            with Session(engine) as session:
                cfg = session.get(FeedImportConfig, cid)
                if cfg and cfg.feed_url:
                    added = await sync_feed(cfg, session)
                    logger.info("Feed import user_id=%d : +%d links", cfg.user_id, added)
        except Exception as exc:
            logger.error("Feed import error config_id=%d : %s", cid, _sync_error(exc))


def _sync_error(exc: Exception) -> str:
    """Message sans l'URL du flux (elle porte un jeton) : les nôtres, ou le type d'erreur."""
    if isinstance(exc, RuntimeError):
        return str(exc)
    return f"Feed unreachable or invalid response ({type(exc).__name__})"


# ── Page de configuration ─────────────────────────────────────────────────────

def _config(session: Session, user_id: int) -> Optional[FeedImportConfig]:
    return session.exec(select(FeedImportConfig).where(FeedImportConfig.user_id == user_id)).first()


@settings_router.get("/settings/feed-import", response_class=HTMLResponse)
async def feed_import_form(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request,
        "settings/feed_import.html",
        {"user": current_user, "config": _config(session, current_user.id),
         **sidebar_data(session, current_user.id)},
    )


@settings_router.post("/settings/feed-import")
async def feed_import_save(
    feed_url: str = Form(""),
    group_name: str = Form("Reddit"),
    is_enabled: Optional[str] = Form(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    config = _config(session, current_user.id) or FeedImportConfig(user_id=current_user.id)
    url = feed_url.strip()
    if url:  # vide = garder l'URL actuelle (elle n'est jamais réaffichée)
        if not await _assert_public_url(url):
            raise HTTPException(status_code=400, detail="Invalid feed URL or private address")
        url = url[:2000]
        if not config.feed_url or decrypt(config.feed_url) != url:
            # Nouveau flux : compteurs et URL vues repartent de zéro.
            config.last_sync, config.synced_count, config.seen_urls = None, 0, ""
        config.feed_url = encrypt(url)
    config.group_name = group_name.strip()[:200] or "Reddit"
    config.is_enabled = is_enabled is not None
    session.add(config)
    session.commit()
    return RedirectResponse(url="/settings/feed-import?saved=1", status_code=303)


@settings_router.post("/settings/feed-import/sync-now", dependencies=[Depends(rate_limit(3, 3600))])
async def feed_import_sync_now(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    config = _config(session, current_user.id)
    if not config or not config.feed_url:
        raise HTTPException(status_code=400, detail="Missing feed configuration")
    try:
        added = await sync_feed(config, session)
    except Exception as exc:
        logger.warning("Feed import failed user_id=%d : %s", current_user.id, _sync_error(exc))
        raise HTTPException(status_code=502, detail=_sync_error(exc))
    return RedirectResponse(url=f"/settings/feed-import?synced={added}", status_code=303)
