import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlmodel import Session, select

from ..auth import get_current_user
from ..crypto import decrypt, encrypt, hmac_key
from ..database import engine, get_session
from ..models import FreshRSSConfig, Group, Link, LinkGroupLink, User
from ..ratelimit import rate_limit
from .links import _safe_url
from ..templates_cfg import templates
from ..utils import refresh_link_fts, sidebar_data

logger = logging.getLogger("excerpta.freshrss")

settings_router = APIRouter()
api_router = APIRouter(prefix="/api/v1")


# ── Greader API helpers ───────────────────────────────────────────────────────

async def _greader_auth(base_url: str, user: str, token: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{base_url}/api/greader.php/accounts/ClientLogin",
            data={"Email": user, "Passwd": token},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"Auth FreshRSS échouée (HTTP {resp.status_code})")
    for line in resp.text.splitlines():
        if line.startswith("Auth="):
            return line[5:].strip()
    raise RuntimeError("FreshRSS n'a pas retourné de token Auth")


async def _greader_starred(base_url: str, auth: str) -> list[dict]:
    url = (
        f"{base_url}/api/greader.php/reader/api/0/stream/contents"
        "/user/-/state/com.google/starred"
    )
    headers = {"Authorization": f"GoogleLogin auth={auth}"}
    items: list[dict] = []
    continuation: str | None = None
    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            params: dict = {"output": "json", "n": 200}
            if continuation:
                params["c"] = continuation
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                break
            data = resp.json()
            batch = data.get("items", [])
            items.extend(batch)
            continuation = data.get("continuation")
            if not continuation or len(batch) < 200:
                break
    return items


def _extract_url(item: dict) -> str | None:
    for src in (item.get("canonical") or [], item.get("alternate") or []):
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

async def sync_user(config: FreshRSSConfig, session: Session) -> int:
    """Sync les étoilés FreshRSS d'un utilisateur. Retourne le nombre de liens ajoutés."""
    auth = await _greader_auth(config.freshrss_url, config.freshrss_user, decrypt(config.freshrss_token))
    items = await _greader_starred(config.freshrss_url, auth)

    group = session.exec(
        select(Group).where(
            Group.user_id == config.user_id,
            Group.name == config.group_name,
        )
    ).first()
    if not group:
        group = Group(user_id=config.user_id, name=config.group_name)
        session.add(group)
        session.flush()

    candidate_urls = [u for u in (_extract_url(i) for i in items) if u]
    if not candidate_urls:
        return 0

    params: dict = {"uid": config.user_id}
    for i, u in enumerate(candidate_urls):
        params[f"u{i}"] = u
    placeholders = ", ".join(f":u{i}" for i in range(len(candidate_urls)))
    existing_urls = {
        row[0]
        for row in session.execute(
            text(f"SELECT url FROM links WHERE user_id = :uid AND url IN ({placeholders})"),
            params,
        ).fetchall()
    }

    added = 0
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

        link = Link(
            user_id=config.user_id,
            url=url,
            title=title[:500],
            description=description,
            note="",
            favicon_url="",
            thumbnail_url=_extract_thumbnail(item),
        )
        session.add(link)
        session.flush()
        session.add(LinkGroupLink(link_id=link.id, group_id=group.id))
        session.flush()
        refresh_link_fts(session, link, [])
        existing_urls.add(url)
        added += 1

    config.last_sync = datetime.now(timezone.utc).replace(tzinfo=None)
    config.synced_count += added
    session.add(config)
    session.commit()
    return added


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
        raise HTTPException(status_code=401, detail="Authentification requise")
    computed_hmac = hmac_key(x_api_key)
    user = session.exec(select(User).where(User.api_key_hmac == computed_hmac)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Clé API invalide")
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
            detail="Aucune configuration FreshRSS active pour cet utilisateur",
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
            p = urlparse(url)
            if p.scheme not in ("http", "https") or not p.netloc:
                raise ValueError
        except Exception:
            raise HTTPException(status_code=400, detail="URL FreshRSS invalide")

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


@settings_router.post("/settings/freshrss/sync-now")
async def freshrss_sync_now(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    config = session.exec(
        select(FreshRSSConfig).where(FreshRSSConfig.user_id == current_user.id)
    ).first()
    if not config or not config.freshrss_url:
        raise HTTPException(status_code=400, detail="Configuration FreshRSS manquante")
    try:
        added = await sync_user(config, session)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return RedirectResponse(url=f"/settings/freshrss?synced={added}", status_code=303)
