from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from ..database import get_session
from ..models import Link
from ..templates_cfg import templates

router = APIRouter()


@router.get("/public", response_class=HTMLResponse)
async def public_links(
    request: Request,
    q: Optional[str] = None,
    tag: Optional[str] = None,
    session: Session = Depends(get_session),
):
    links = list(
        session.exec(
            select(Link).where(Link.is_public == True).order_by(Link.created_at.desc())
        ).all()
    )

    if q:
        q_lower = q.lower()
        links = [
            lk for lk in links
            if q_lower in (lk.title or "").lower()
            or q_lower in (lk.description or "").lower()
            or q_lower in (lk.note or "").lower()
            or q_lower in lk.url.lower()
        ]

    if tag:
        links = [lk for lk in links if any(t.name == tag for t in lk.tags)]

    all_tags: list[str] = []
    seen: set[str] = set()
    for lk in links:
        for t in lk.tags:
            if t.name not in seen:
                all_tags.append(t.name)
                seen.add(t.name)
    all_tags.sort()

    return templates.TemplateResponse(
        "public/index.html",
        {
            "request": request,
            "links": links,
            "all_tags": all_tags,
            "current_tag": tag,
            "q": q or "",
            "total": len(links),
        },
    )
