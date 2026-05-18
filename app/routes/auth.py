from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import settings
from ..templates_cfg import templates

router = APIRouter()


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)


@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    safe_error = error if error in ("1", "inactive") else None
    return templates.TemplateResponse(
        "auth/login.html", {"request": request, "error": safe_error}
    )
