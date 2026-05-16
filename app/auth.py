from fastapi import Depends, Request
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
    if not user:
        raise NotAuthenticated()
    return user
