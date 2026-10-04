import hmac
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.access.models import Session, User
from backend.app.access.policy import Access, get_access
from backend.app.infrastructure.database import aware, get_db, utcnow
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.security import csrf_token, token_hash


Db = Annotated[AsyncSession, Depends(get_db, scope="function")]


async def current_user(request: Request, db: Db) -> User:
    token = request.cookies.get("razbor_session", "")
    session = (
        await db.scalar(select(Session).where(Session.token_hash == token_hash(token)))
        if token
        else None
    )
    if session is None or aware(session.expires_at) <= utcnow():
        raise AppError("unauthenticated", "Войдите в аккаунт", 401)
    user = await db.get(User, session.user_id)
    if user is None or not user.active:
        raise AppError("unauthenticated", "Войдите в аккаунт", 401)
    request.state.actor_id = user.id
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        expected = csrf_token(token, request.app.state.settings.secret_key)
        if not hmac.compare_digest(request.headers.get("X-CSRF-Token", ""), expected):
            raise AppError("csrf", "Сессия изменилась. Обновите страницу", 403)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def workspace_access(workspace_id: str, user: CurrentUser, db: Db) -> Access:
    return await get_access(db, user.id, workspace_id)


WorkspaceAccess = Annotated[Access, Depends(workspace_access)]
