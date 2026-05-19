import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth import NotAuthenticated
from .config import settings
from .csrf import csrf_protect
from .database import init_db
from . import models  # noqa: F401
from .routes import auth as auth_router
from .routes import links as links_router
from .routes import tags as tags_router
from .routes import folders as folders_router
from .routes import oidc as oidc_router
from .routes import settings as settings_router
from .routes import public as public_router
from .routes import admin as admin_router
from .routes import api as api_router
from .routes.freshrss import settings_router as freshrss_settings_router
from .routes.freshrss import api_router as freshrss_api_router
from .routes.freshrss import sync_all_enabled

logger = logging.getLogger("excerpta")


async def _freshrss_loop():
    await asyncio.sleep(60)  # délai initial au démarrage
    while True:
        try:
            await sync_all_enabled()
        except Exception:
            logger.exception("Erreur dans la boucle de sync FreshRSS")
        await asyncio.sleep(settings.freshrss_sync_interval * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(_freshrss_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=86400 * 7,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory="/app/static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    nonce = secrets.token_urlsafe(16)
    request.state.nonce = nonce
    response: Response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response
    csp = (
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
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
app.include_router(admin_router.router, dependencies=_csrf)
app.include_router(api_router.router)  # JSON API - pas de CSRF, auth par X-API-Key
app.include_router(freshrss_settings_router, dependencies=_csrf)
app.include_router(freshrss_api_router)  # JSON API FreshRSS - pas de CSRF


@app.get("/health")
def health():
    return {"status": "ok"}
