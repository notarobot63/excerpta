import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlmodel import Session, select

from ..auth import get_current_user
from ..crypto import decrypt, encrypt
from ..database import engine, get_session
from ..models import Folder, FreshRSSConfig, Link, User
from ..ratelimit import rate_limit
from .links import _assert_public_url, _fetch_meta, _safe_url
from ..templates_cfg import templates
from ..utils import refresh_link_fts, resolve_api_user, sidebar_data

logger = logging.getLogger("excerpta.freshrss")

settings_router = APIRouter()
api_router = APIRouter(prefix="/api/v1")

_http_client: httpx.AsyncClient | None = None


def set_http_client(client: httpx.AsyncClient) -> None:
    global _http_client
    _http_client = client


# ── Greader API helpers ───────────────────────────────────────────────────────

# Plafond d'articles étoilés importés en une passe (anti-DoS mémoire)
_MAX_STARRED_ITEMS = 5000

async def _greader_auth(base_url: str, user: str, token: str) -> str:
    resp = await _http_client.post(
        f"{base_url}/api/greader.php/accounts/ClientLogin",
        data={"Email": user, "Passwd": token},
        timeout=10,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Auth FreshRSS échouée (HTTP {resp.status_code})")
    for line in resp.text.splitlines():
        if line.startswith("Auth="):
            return line[5:].strip()
    raise RuntimeError("FreshRSS n'a pas retourné de token Auth")


async def _greader_token(base_url: str, auth: str) -> str:
    resp = await _http_client.get(
        f"{base_url}/api/greader.php/reader/api/0/token",
        headers={"Authorization": f"GoogleLogin auth={auth}"},
        timeout=10,
    )
    return resp.text.strip() if resp.status_code == 200 else ""


async def unstar_item(config: "FreshRSSConfig", item_id: str) -> bool:
    """Déséttoile un article FreshRSS. Retourne True si succès."""
    try:
        auth = await _greader_auth(config.freshrss_url, config.freshrss_user, decrypt(config.freshrss_token))
        t_token = await _greader_token(config.freshrss_url, auth)
        resp = await _http_client.post(
            f"{config.freshrss_url}/api/greader.php/reader/api/0/edit-tag",
            headers={"Authorization": f"GoogleLogin auth={auth}"},
            data={"i": item_id, "r": "user/-/state/com.google/starred", "T": t_token},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("FreshRSS unstar failed item_id=%s : %s", item_id, exc)
        return False


async def _greader_starred(base_url: str, auth: str) -> list[dict]:
    url = (
        f"{base_url}/api/greader.php/reader/api/0/stream/contents"
        "/user/-/state/com.google/starred"
    )
    headers = {"Authorization": f"GoogleLogin auth={auth}"}
    items: list[dict] = []
    continuation: str | None = None
    while True:
        params: dict = {"output": "json", "n": 200}
        if continuation:
            params["c"] = continuation
        resp = await _http_client.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            break
        data = resp.json()
        batch = data.get("items", [])
        items.extend(batch)
        # Plafond anti-DoS : borne l'ingestion mémoire sur un compte hostile/volumineux
        if len(items) >= _MAX_STARRED_ITEMS:
            del items[_MAX_STARRED_ITEMS:]
            break
        continuation = data.get("continuation")
        if not continuation or len(batch) < 200:
            break
    return items


def _extract_url(item: dict) -> str | None:
    for src in (item.get("canonical") or []) + (item.get("alternate") or []):
        href = src.get("href", "")
        if href.startswith("http"):
            return href
    return None


def _extract_thumbnail(item: dict) -> str:
    """Extrait un thumbnail depuis un item GReader (enclosure ou première <img> du summary)."""
    for enc in item.get("enclosure") or []:
        href = enc.get("href", "")
        mime = enc.get("type", "")
        if href.startswith("http") and mime.startswith("image/") and _safe_url(href):
            return href
    summary = item.get("summary") or {}
    content = summary.get("content", "")
    if content:
        soup = BeautifulSoup(content, "html.parser")
        for img in soup.find_all("img", src=True):
            src = img["src"].strip()
            if src.startswith("http") and _safe_url(src):
                return src
    return ""


# ── Sync core ─────────────────────────────────────────────────────────────────

async def _refresh_new_links_bg(link_ids: list[int]) -> None:
    """Complète favicon/thumbnail/description sur les liens nouvellement importés."""
    from ..database import engine as db_engine
    for link_id in link_ids:
        try:
            with Session(db_engine) as session:
                link = session.get(Link, link_id)
                if not link:
                    continue
                url = link.url
            meta = await _fetch_meta(url)
            with Session(db_engine) as session:
                link = session.get(Link, link_id)
                if not link:
                    continue
                changed = False
                if not link.thumbnail_url and meta.get("thumbnail_url"):
                    link.thumbnail_url = meta["thumbnail_url"]
                    changed = True
                if not link.description and meta.get("description"):
                    link.description = meta["description"]
                    changed = True
                if not link.favicon_url and meta.get("favicon_url"):
                    link.favicon_url = meta["favicon_url"]
                    changed = True
                if changed:
                    session.add(link)
                    session.commit()
        except Exception:
            pass


async def _assert_safe_freshrss_url(url: str) -> None:
    if not await _assert_public_url(url):
        raise ValueError("URL FreshRSS invalide ou pointant vers une adresse privée")


async def sync_user(config: FreshRSSConfig, session: Session) -> int:
    """Sync les étoilés FreshRSS d'un utilisateur. Retourne le nombre de liens ajoutés."""
    try:
        await _assert_safe_freshrss_url(config.freshrss_url)
    except ValueError as exc:
        raise RuntimeError(str(exc))
    auth = await _greader_auth(config.freshrss_url, config.freshrss_user, decrypt(config.freshrss_token))
    items = await _greader_starred(config.freshrss_url, auth)

    folder = session.exec(
        select(Folder).where(
            Folder.user_id == config.user_id,
            Folder.name == config.group_name,
        )
    ).first()
    if not folder:
        folder = Folder(user_id=config.user_id, name=config.group_name)
        session.add(folder)
        session.flush()

    candidate_urls = [u for u in (_extract_url(i) for i in items) if u]
    if not candidate_urls:
        return 0

    params: dict = {"uid": config.user_id}
    for i, u in enumerate(candidate_urls):
        params[f"u{i}"] = u
    placeholders = ", ".join(f":u{i}" for i in range(len(candidate_urls)))
    existing_rows = session.execute(
        text(f"SELECT id, url, freshrss_item_id, folder_id FROM links WHERE user_id = :uid AND url IN ({placeholders})"),
        params,
    ).fetchall()
    existing_urls = {row[1] for row in existing_rows}
    # Backfill : associer l'ID GReader aux liens déjà importés qui ne l'ont pas encore
    url_to_item_id = {_extract_url(i): i.get("id") for i in items if _extract_url(i) and i.get("id")}
    # Self-healing : un lien encore étoilé mais sorti du dossier FreshRSS doit
    # être désétoilé (rattrape les existants + les désétoilages au déplacement
    # qui auraient échoué). Tous les existing_rows correspondent à des items
    # actuellement étoilés (URLs issues de items).
    orphan_item_ids: list[str] = []
    for link_id, url, current_item_id, link_folder_id in existing_rows:
        if current_item_id is None and url in url_to_item_id:
            session.execute(
                text("UPDATE links SET freshrss_item_id = :iid WHERE id = :lid"),
                {"iid": url_to_item_id[url], "lid": link_id},
            )
        if link_folder_id != folder.id:
            iid = current_item_id or url_to_item_id.get(url)
            if iid:
                orphan_item_ids.append(iid)
    if orphan_item_ids:
        await asyncio.gather(*(unstar_item(config, iid) for iid in orphan_item_ids))

    new_links: list[Link] = []
    for item in items:
        url = _extract_url(item)
        if not url or url in existing_urls:
            continue

        title = (item.get("title") or "").strip() or url
        description = ""
        summary = item.get("summary") or {}
        if summary.get("content"):
            description = BeautifulSoup(
                summary["content"], "html.parser"
            ).get_text(" ", strip=True)[:2000]

        pub_ts = item.get("published")
        try:
            created = datetime.fromtimestamp(int(pub_ts), tz=timezone.utc).replace(tzinfo=None) if pub_ts else None
        except (ValueError, OSError):
            created = None

        link = Link(
            user_id=config.user_id,
            url=url,
            title=title[:500],
            description=description,
            note="",
            favicon_url="",
            thumbnail_url=_extract_thumbnail(item),
            folder_id=folder.id,
            freshrss_item_id=item.get("id") or None,
            **({"created_at": created, "updated_at": created} if created else {}),
        )
        session.add(link)
        new_links.append(link)
        existing_urls.add(url)

    if new_links:
        session.flush()  # un seul flush pour assigner tous les IDs
        for link in new_links:
            refresh_link_fts(session, link, [])

    config.last_sync = datetime.now(timezone.utc).replace(tzinfo=None)
    config.synced_count += len(new_links)
    session.add(config)
    session.commit()

    if new_links:
        asyncio.create_task(_refresh_new_links_bg([l.id for l in new_links]))

    return len(new_links)


async def sync_all_enabled() -> None:
    """Parcourt tous les utilisateurs FreshRSS actifs et les synchronise."""
    with Session(engine) as session:
        configs = list(
            session.exec(
                select(FreshRSSConfig).where(FreshRSSConfig.is_enabled == True)  # noqa: E712
            ).all()
        )
    for cfg in configs:
        try:
            with Session(engine) as session:
                fresh = session.get(FreshRSSConfig, cfg.id)
                if fresh:
                    added = await sync_user(fresh, session)
                    logger.info("FreshRSS sync user_id=%d : +%d liens", fresh.user_id, added)
        except Exception as exc:
            logger.error("FreshRSS sync error user_id=%d : %s", cfg.user_id, exc)


# ── API endpoint (pour cron ou appel externe) ─────────────────────────────────

async def _get_api_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    x_api_key = request.headers.get("X-API-Key")
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Authentication required")
    user = resolve_api_user(session, x_api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return user


@api_router.post("/freshrss/sync", dependencies=[Depends(rate_limit(10, 60))])
async def api_freshrss_sync(
    user: User = Depends(_get_api_user),
    session: Session = Depends(get_session),
):
    config = session.exec(
        select(FreshRSSConfig).where(FreshRSSConfig.user_id == user.id)
    ).first()
    if not config or not config.is_enabled:
        raise HTTPException(
            status_code=404,
            detail="No active FreshRSS configuration for this user",
        )
    added = await sync_user(config, session)
    return {
        "added": added,
        "total": config.synced_count,
        "last_sync": config.last_sync.isoformat() if config.last_sync else None,
    }


# ── Page de configuration ─────────────────────────────────────────────────────

@settings_router.get("/settings/freshrss", response_class=HTMLResponse)
async def freshrss_settings_form(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    config = session.exec(
        select(FreshRSSConfig).where(FreshRSSConfig.user_id == current_user.id)
    ).first()
    return templates.TemplateResponse(
        "settings/freshrss.html",
        {
            "request": request,
            "user": current_user,
            "config": config,
            **sidebar_data(session, current_user.id),
        },
    )


@settings_router.post("/settings/freshrss")
async def freshrss_settings_save(
    request: Request,
    freshrss_url: str = Form(""),
    freshrss_user: str = Form(""),
    freshrss_token: str = Form(""),
    group_name: str = Form("FreshRSS"),
    is_enabled: Optional[str] = Form(default=None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    url = freshrss_url.strip().rstrip("/")
    if url:
        try:
            await _assert_safe_freshrss_url(url)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid FreshRSS URL or private address")

    config = session.exec(
        select(FreshRSSConfig).where(FreshRSSConfig.user_id == current_user.id)
    ).first()
    if not config:
        config = FreshRSSConfig(user_id=current_user.id)

    config.freshrss_url = url
    config.freshrss_user = freshrss_user.strip()[:200]
    if freshrss_token.strip():  # ne pas écraser avec vide si champ laissé vide
        config.freshrss_token = encrypt(freshrss_token.strip()[:500])
    config.group_name = group_name.strip()[:200] or "FreshRSS"
    config.is_enabled = is_enabled is not None
    session.add(config)
    session.commit()
    return RedirectResponse(url="/settings/freshrss?saved=1", status_code=303)


@settings_router.post("/settings/freshrss/sync-now",
                       dependencies=[Depends(rate_limit(3, 3600))])
async def freshrss_sync_now(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    config = session.exec(
        select(FreshRSSConfig).where(FreshRSSConfig.user_id == current_user.id)
    ).first()
    if not config or not config.freshrss_url:
        raise HTTPException(status_code=400, detail="Missing FreshRSS configuration")
    try:
        added = await sync_user(config, session)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return RedirectResponse(url=f"/settings/freshrss?synced={added}", status_code=303)
