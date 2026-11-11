"""Import d'un flux RSS/Atom : chaque entrée devient un lien.

Pensé pour le flux privé des éléments enregistrés Reddit (le lien d'une
entrée y est la discussion), mais vaut pour tout flux. Contrairement à
FreshRSS, rien n'est renvoyé à la source : le dossier (racine) est retrouvé
par son nom, et un renommage en fait seulement créer un nouveau. Les URL déjà
vues sont mémorisées : un lien supprimé ou purgé ne revient pas.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
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


MIN_INTERVAL, MAX_INTERVAL = 5, 1440  # minutes ; plancher pour ne pas marteler les sites


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Par compte : deux flux d'un même compte peuvent porter la même URL.
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
        config.last_sync = _now()
        config.last_error = ""
        config.synced_count += len(new_links)
        session.add(config)
        session.commit()

        if new_links:
            spawn(refresh_links_meta([(l.id, l.url) for l in new_links]), name="feed-import-refresh")
        return len(new_links)


def _sync_error(exc: Exception) -> str:
    """Message sans l'URL du flux (elle porte un jeton) : les nôtres, ou le type d'erreur."""
    if isinstance(exc, RuntimeError):
        return str(exc)
    return f"Feed unreachable or invalid response ({type(exc).__name__})"


async def _sync_recording_error(config: FeedImportConfig, session: Session) -> int:
    """sync_feed, en notant l'échec sur le flux (affiché dans les réglages).

    L'heure de la tentative est notée aussi : un flux en erreur attend son
    intervalle au lieu d'être retenté chaque minute.
    """
    try:
        return await sync_feed(config, session)
    except Exception as exc:
        session.rollback()
        config.last_error, config.last_sync = _sync_error(exc)[:500], _now()
        session.add(config)
        session.commit()
        raise


def _is_due(config: FeedImportConfig, now: datetime) -> bool:
    return config.last_sync is None or config.last_sync + timedelta(minutes=config.interval_minutes) <= now


async def sync_due_feeds() -> None:
    """Synchronise les flux actifs dont l'intervalle est écoulé (comptes actifs seulement)."""
    now = _now()
    with Session(engine) as session:
        due = [c.id for c in session.exec(
            select(FeedImportConfig)
            .join(User, User.id == FeedImportConfig.user_id)
            .where(FeedImportConfig.is_enabled == True, User.is_active == True)  # noqa: E712
        ).all() if c.feed_url and _is_due(c, now)]
    for cid in due:
        try:
            with Session(engine) as session:
                cfg = session.get(FeedImportConfig, cid)
                if cfg:
                    added = await _sync_recording_error(cfg, session)
                    logger.info("Feed import #%d user_id=%d : +%d links", cid, cfg.user_id, added)
        except Exception as exc:
            logger.error("Feed import error #%d : %s", cid, _sync_error(exc))


# ── Page de configuration ─────────────────────────────────────────────────────

def _owned(session: Session, user_id: int, feed_id: int) -> FeedImportConfig:
    config = session.get(FeedImportConfig, feed_id)
    if not config or config.user_id != user_id:
        raise HTTPException(status_code=404)
    return config


def _interval(raw: int) -> int:
    return min(max(raw, MIN_INTERVAL), MAX_INTERVAL)


async def _checked_url(raw: str) -> str:
    url = raw.strip()[:2000]
    if url and not await _assert_public_url(url):
        raise HTTPException(status_code=400, detail="Invalid feed URL or private address")
    return url


@settings_router.get("/settings/feed-import", response_class=HTMLResponse)
async def feed_import_form(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    feeds = session.exec(
        select(FeedImportConfig).where(FeedImportConfig.user_id == current_user.id).order_by(FeedImportConfig.id)
    ).all()
    return templates.TemplateResponse(
        request,
        "settings/feed_import.html",
        {"user": current_user, "feeds": feeds, "min_interval": MIN_INTERVAL, "max_interval": MAX_INTERVAL,
         **sidebar_data(session, current_user.id)},
    )


@settings_router.post("/settings/feed-import", dependencies=[Depends(rate_limit(20, 3600))])
async def feed_import_add(
    feed_url: str = Form(...),
    group_name: str = Form("Reddit"),
    interval_minutes: int = Form(30),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    url = await _checked_url(feed_url)
    if not url:
        raise HTTPException(status_code=400, detail="Missing feed URL")
    session.add(FeedImportConfig(
        user_id=current_user.id, feed_url=encrypt(url), group_name=group_name.strip()[:200] or "Reddit",
        interval_minutes=_interval(interval_minutes), is_enabled=True,
    ))
    session.commit()
    return RedirectResponse(url="/settings/feed-import?saved=1", status_code=303)


@settings_router.post("/settings/feed-import/{feed_id}")
async def feed_import_save(
    feed_id: int,
    feed_url: str = Form(""),
    group_name: str = Form("Reddit"),
    interval_minutes: int = Form(30),
    is_enabled: Optional[str] = Form(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    config = _owned(session, current_user.id, feed_id)
    url = await _checked_url(feed_url)
    if url:  # vide = garder l'URL actuelle (elle n'est jamais réaffichée)
        if decrypt(config.feed_url) != url:
            # Nouveau flux : compteurs, erreur et URL vues repartent de zéro.
            config.last_sync, config.synced_count, config.seen_urls, config.last_error = None, 0, "", ""
        config.feed_url = encrypt(url)
    config.group_name = group_name.strip()[:200] or "Reddit"
    config.interval_minutes = _interval(interval_minutes)
    config.is_enabled = is_enabled is not None
    session.add(config)
    session.commit()
    return RedirectResponse(url="/settings/feed-import?saved=1", status_code=303)


@settings_router.post("/settings/feed-import/{feed_id}/delete")
async def feed_import_delete(
    feed_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    # Les liens déjà importés restent, avec leur dossier.
    session.delete(_owned(session, current_user.id, feed_id))
    session.commit()
    return RedirectResponse(url="/settings/feed-import?deleted=1", status_code=303)


@settings_router.post("/settings/feed-import/{feed_id}/sync-now", dependencies=[Depends(rate_limit(10, 3600))])
async def feed_import_sync_now(
    feed_id: int,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    config = _owned(session, current_user.id, feed_id)
    try:
        added = await _sync_recording_error(config, session)
    except Exception as exc:
        # L'erreur est affichée sur la carte du flux plutôt qu'en page d'erreur brute.
        logger.warning("Feed import failed #%d : %s", feed_id, _sync_error(exc))
        return RedirectResponse(url="/settings/feed-import?failed=1", status_code=303)
    return RedirectResponse(url=f"/settings/feed-import?synced={added}", status_code=303)
