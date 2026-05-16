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
from .routes import groups as groups_router
from .routes import oidc as oidc_router
from .routes import settings as settings_router
from .routes import public as public_router
from .routes import admin as admin_router
from .routes import api as api_router

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https: http:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    max_age=86400 * 7,
    same_site="lax",
)
app.mount("/static", StaticFiles(directory="/app/static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["Content-Security-Policy"] = _CSP
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
app.include_router(groups_router.router, dependencies=_csrf)
app.include_router(settings_router.router, dependencies=_csrf)
app.include_router(public_router.router)  # pas de CSRF ni auth
app.include_router(admin_router.router, dependencies=_csrf)
app.include_router(api_router.router)  # JSON API - pas de CSRF, auth par X-API-Key


@app.get("/health")
def health():
    return {"status": "ok"}
