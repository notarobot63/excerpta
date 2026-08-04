"""Préchauffage du cache d'images et proxy d'images (favicons/vignettes)."""
import asyncio
import hashlib
import time
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import Session, select

from ...auth import get_current_user
from ...database import engine as db_engine, get_session
from ...demo import demo_active, is_demo_user
from ...models import Link, User
from ...ratelimit import rate_limit
from .net_guard import _TooLarge, _assert_public_url, _read_limited, _safe_stream, _safe_url

router = APIRouter()

_IMG_CACHE_TTL = 86400
_IMG_CACHE_MAX = 1000
# Le plafond en nombre d'entrées ne borne pas la mémoire : 1000 images de 10 Mo
# font 10 Go. Un second plafond, en octets cumulés, évince jusqu'à repasser
# dessous. Les deux servent : l'un limite le nombre d'objets, l'autre l'empreinte.
_IMG_CACHE_MAX_BYTES = 128 * 1024 * 1024
_img_cache: OrderedDict = OrderedDict()
_img_cache_bytes = 0
_img_cache_lock = asyncio.Lock()

_MAX_IMG_BYTES = 10 * 1024 * 1024   # 10 Mo pour une image proxifiée
_FORBIDDEN_IMG_TYPES = {"image/svg+xml", "image/svg"}


def _cache_store(url: str, expiry: float, content_type: str | None, content: bytes | None) -> None:
    """Insère une entrée et évince les plus anciennes (appelant : verrou tenu)."""
    global _img_cache_bytes
    previous = _img_cache.pop(url, None)
    if previous and previous[2]:
        _img_cache_bytes -= len(previous[2])
    _img_cache[url] = (expiry, content_type, content)
    _img_cache_bytes += len(content) if content else 0
    while _img_cache and (len(_img_cache) > _IMG_CACHE_MAX or _img_cache_bytes > _IMG_CACHE_MAX_BYTES):
        _, evicted = _img_cache.popitem(last=False)
        if evicted[2]:
            _img_cache_bytes -= len(evicted[2])


def _cache_drop(url: str) -> None:
    """Retire une entrée (appelant : verrou tenu)."""
    global _img_cache_bytes
    entry = _img_cache.pop(url, None)
    if entry and entry[2]:
        _img_cache_bytes -= len(entry[2])


async def warm_img_cache() -> None:
    await asyncio.sleep(10)
    with Session(db_engine) as db:
        rows = db.exec(select(Link.favicon_url, Link.thumbnail_url)).all()
    urls: set = set()
    for row in rows:
        if row.favicon_url:
            urls.add(row.favicon_url)
        if row.thumbnail_url:
            urls.add(row.thumbnail_url)
    sem = asyncio.Semaphore(8)

    async def _fetch(url: str) -> None:
        async with sem:
            async with _img_cache_lock:
                cached = _img_cache.get(url)
                if cached and time.time() < cached[0]:
                    return
            try:
                if not await _assert_public_url(url):
                    return
                async with _safe_stream("GET", url, headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                    "Accept": "image/avif,image/webp,image/png,image/*;q=0.8,*/*;q=0.5",
                }, timeout=5) as resp:
                    if resp.status_code != 200:
                        return
                    ct = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                    if not ct.startswith("image/") or ct in _FORBIDDEN_IMG_TYPES:
                        return
                    content = await _read_limited(resp, _MAX_IMG_BYTES)
                async with _img_cache_lock:
                    _cache_store(url, time.time() + _IMG_CACHE_TTL, ct, content)
            except Exception:
                pass

    url_list = list(urls)
    for i in range(0, len(url_list), 50):
        await asyncio.gather(*[_fetch(u) for u in url_list[i:i + 50]])
        await asyncio.sleep(0)


@router.get("/proxy/img", dependencies=[Depends(rate_limit(120, 60))])
async def proxy_image(
    request: Request,
    url: str,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    # Ce proxy récupère une URL passée en paramètre : en démo, c'est une sortie
    # réseau pilotable par un inconnu. Les liens du catalogue n'ont ni favicon ni
    # vignette, la route n'a donc aucune raison d'être appelée. Le test précède
    # session.close(), qui détacherait l'objet User.
    if demo_active() and is_demo_user(user):
        raise HTTPException(status_code=404)

    # Idem : pas besoin de la DB au-delà de l'auth, et cette route est appelée
    # en rafale (une par vignette de lien) au chargement d'une page.
    session.close()
    if not _safe_url(url):
        raise HTTPException(status_code=400)
    etag = hashlib.md5(url.encode()).hexdigest()
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    cached = _img_cache.get(url)
    if cached:
        expiry, content_type, content = cached
        if time.time() < expiry:
            async with _img_cache_lock:
                _img_cache.move_to_end(url)
            if content is None:
                raise HTTPException(status_code=404)
            return Response(
                content=content,
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400", "ETag": etag, "Cross-Origin-Resource-Policy": "cross-origin"},
            )
        async with _img_cache_lock:
            _cache_drop(url)

    if not await _assert_public_url(url):
        raise HTTPException(status_code=400)
    try:
        async with _safe_stream("GET", url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Accept": "image/avif,image/webp,image/png,image/*;q=0.8,*/*;q=0.5",
        }, timeout=5) as resp:
            if resp.status_code != 200:
                async with _img_cache_lock:
                    _cache_store(url, time.time() + 3600, None, None)
                raise HTTPException(status_code=404)
            content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            # SVG exclu : servi depuis notre origine, il peut porter du script.
            if not content_type.startswith("image/") or content_type in _FORBIDDEN_IMG_TYPES:
                raise HTTPException(status_code=415)
            try:
                content = await _read_limited(resp, _MAX_IMG_BYTES)
            except _TooLarge:
                raise HTTPException(status_code=413)
        async with _img_cache_lock:
            _cache_store(url, time.time() + _IMG_CACHE_TTL, content_type, content)
        return Response(
            content=content,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=86400", "ETag": etag, "Cross-Origin-Resource-Policy": "cross-origin"},
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502)
