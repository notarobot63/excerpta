import asyncio
import logging
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import PlainTextResponse

from .auth import NotAuthenticated
from .config import settings
from .csrf import csrf_protect
from .database import init_db, cleanup_freshrss_tag
from .i18n import LocaleMiddleware
from . import models  # noqa: F401
from .routes import auth as auth_router
from .routes import links as links_router
from .routes import tags as tags_router
from .routes import folders as folders_router
from .routes import lang as lang_router
from .routes import oidc as oidc_router
from .routes import settings as settings_router
from .routes import public as public_router
from .routes import admin as admin_router
from .routes import api as api_router
from .routes.freshrss import settings_router as freshrss_settings_router
from .routes.freshrss import api_router as freshrss_api_router
from .routes.freshrss import sync_all_enabled, set_http_client as freshrss_set_client
from .routes.links import warm_img_cache, set_http_client as links_set_client
from .routes.settings import set_http_client as settings_set_client

logger = logging.getLogger("excerpta")

_HOST_HEADER_RE = re.compile(r"^(?P<host>[A-Za-z0-9.-]+)(:(?P<port>\d{1,5}))?$")


class StrictHostMiddleware:
    # Valide le header Host au format `hostname[:port numérique]`. Contrairement
    # à TrustedHostMiddleware (qui ne fait qu'un split(":")[0] et ignore tout ce
    # qui suit, donc laisse passer "host:evil.attacker.example" ou
    # "host:99999999999999999"), on vérifie aussi la partie port : ces valeurs
    # font crasher la reconstruction de request.url plus loin dans la pile
    # Starlette (CVE-2026-48710 "BadHost").
    def __init__(self, app, allowed_hosts):
        self.app = app
        self.allowed_hosts = set(allowed_hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        host_header = Headers(scope=scope).get("host", "")
        match = _HOST_HEADER_RE.match(host_header)
        if not match or match.group("host") not in self.allowed_hosts:
            response = PlainTextResponse("Invalid host header", status_code=400)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def _freshrss_loop():
    await asyncio.sleep(60)  # délai initial au démarrage
    while True:
        try:
            await sync_all_enabled()
        except Exception:
            logger.exception("Error in the FreshRSS sync loop")
        await asyncio.sleep(settings.freshrss_sync_interval * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(
        follow_redirects=True,
        max_redirects=5,
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    ) as http_client:
        links_set_client(http_client)
        freshrss_set_client(http_client)
        settings_set_client(http_client)
        init_db()
        cleanup_freshrss_tag()
        task = asyncio.create_task(_freshrss_loop())
        task_warmup = asyncio.create_task(warm_img_cache())
        try:
            yield
        finally:
            task.cancel()
            task_warmup.cancel()
            for t in [task, task_warmup]:
                try:
                    await t
                except asyncio.CancelledError:
                    pass


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
_allowed_host = urlparse(settings.base_url).hostname or "localhost"
_extra_hosts = [h.strip() for h in settings.extra_allowed_hosts.split(",") if h.strip()]
# "testserver" = host par défaut du TestClient Starlette. Autorisé uniquement
# sous TESTING=1 : en production c'était un contournement du contrôle de Host.
if settings.testing:
    _extra_hosts.append("testserver")
# ATTENTION à l'ordre : Starlette insère chaque add_middleware en tête de pile,
# donc le DERNIER ajouté s'exécute en PREMIER. StrictHostMiddleware doit rester
# le dernier appel de ce bloc pour rejeter un Host invalide avant que la session
# ne soit déchiffrée et que la réponse ne soit compressée.
# LocaleMiddleware est ajouté en premier, donc s'exécute en dernier : il lit la
# préférence de langue dans la session, qui doit déjà avoir été déchiffrée.
app.add_middleware(LocaleMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=86400 * 7,
    same_site="lax",
    # Cookie de session jamais transmis en clair. Désactivable pour un accès
    # LAN en HTTP pur, où le flag Secure empêcherait toute connexion.
    https_only=settings.session_cookie_secure,
)
# Compression des réponses (HTML/CSS/JS/JSON) > 1 Ko
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(StrictHostMiddleware, allowed_hosts=[_allowed_host, *_extra_hosts])
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.middleware("http")
async def block_unauthenticated_api_options(request: Request, call_next):
    # Starlette répond aux OPTIONS non enregistrées par un 405 + en-tête
    # Allow avant même que la dépendance d'auth (X-API-Key) ne s'exécute,
    # ce qui permet de cartographier les verbes de /api/v1/* sans credentials.
    # Comme l'API n'a pas besoin d'un vrai preflight CORS (aucun CORSMiddleware
    # n'est configuré), on renvoie directement un 401 générique, sans Allow.
    if request.method == "OPTIONS" and request.url.path.startswith("/api/v1/"):
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    nonce = secrets.token_urlsafe(16)
    request.state.nonce = nonce
    response: Response = await call_next(request)
    if request.url.path.startswith("/static/"):
        # Asset versionné (?v=) → immuable 1 an ; sinon cache court (favicons, libs)
        if request.query_params.get("v"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response
    csp = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data: https: http:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url="/auth/login")


_csrf = [Depends(csrf_protect)]

app.include_router(auth_router.router)
app.include_router(oidc_router.router)
app.include_router(links_router.router, dependencies=_csrf)
app.include_router(tags_router.router, dependencies=_csrf)
app.include_router(folders_router.router, dependencies=_csrf)
app.include_router(settings_router.router, dependencies=_csrf)
app.include_router(public_router.router)  # pas de CSRF ni auth
app.include_router(lang_router.router)  # GET bénin, accessible sans auth
app.include_router(admin_router.router, dependencies=_csrf)
app.include_router(api_router.router)  # JSON API - pas de CSRF, auth par X-API-Key
app.include_router(freshrss_settings_router, dependencies=_csrf)
app.include_router(freshrss_api_router)  # JSON API FreshRSS - pas de CSRF


@app.get("/health")
def health():
    # Pas de version/commit hash ici : endpoint public non-authentifié,
    # réservé aux sondes de liveness (Docker healthcheck, UptimeKuma).
    return {"status": "ok"}
