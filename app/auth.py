from fastapi import Depends, HTTPException, Request
from sqlmodel import Session

from .database import get_session
from .models import User


class NotAuthenticated(Exception):
    pass


async def get_current_user(request: Request, session: Session = Depends(get_session)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise NotAuthenticated()
    user = session.get(User, user_id)
    if not user or not user.is_active:
        raise NotAuthenticated()
    if request.session.get("session_version") != user.session_version:
        request.session.clear()
        raise NotAuthenticated()
    return user


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Accès réservé à l'administrateur")
    return user
