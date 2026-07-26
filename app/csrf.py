import secrets

from fastapi import HTTPException, Request
from markupsafe import Markup


def _get_or_create_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_hex(32)
        request.session["csrf_token"] = token
    return token


async def csrf_protect(request: Request) -> None:
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return
    if request.url.path.startswith("/auth/oidc"):
        return
    expected = request.session.get("csrf_token")
    if not expected:
        raise HTTPException(status_code=403, detail="CSRF token missing")
    # Accepter le token via header (requêtes JSON/AJAX same-origin)
    header_token = request.headers.get("X-CSRF-Token", "")
    if header_token and secrets.compare_digest(header_token, expected):
        return
    form = await request.form()
    if not secrets.compare_digest(str(form.get("csrf_token", "")), expected):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def csrf_input(request: Request) -> Markup:
    token = _get_or_create_token(request)
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')
