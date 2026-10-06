import hmac
import secrets
from datetime import timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend.app.access.dependencies import CurrentUser, Db, WorkspaceAccess
from backend.app.access.models import LoginAttempt, Membership, Session, Setup, User, Workspace
from backend.app.access.policy import from_membership
from backend.app.access.api.schemas import Bootstrap, Login, MemberCreate, MemberUpdate
from backend.app.infrastructure.database import new_id, utcnow
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.security import (
    csrf_token,
    hash_password,
    password_work,
    token_hash,
    verify_password,
)


router = APIRouter()


async def session_payload(db, user, token, settings):
    records = (
        await db.execute(
            select(Workspace, Membership)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user.id, Membership.active.is_(True))
            .order_by(Workspace.name)
        )
    ).all()
    return {
        "user": {"id": user.id, "email": user.email, "name": user.name},
        "csrf_token": csrf_token(token, settings.secret_key),
        "workspaces": [
            {
                "id": workspace.id,
                "name": workspace.name,
                "role": member.role,
                "capabilities": sorted(from_membership(member).capabilities),
                "all_stores": member.all_stores,
                "store_ids": member.store_ids,
            }
            for workspace, member in records
        ],
    }


async def establish_session(db, user, request, response):
    token = secrets.token_urlsafe(48)
    settings = request.app.state.settings
    db.add(
        Session(
            user_id=user.id,
            token_hash=token_hash(token),
            expires_at=utcnow() + timedelta(seconds=settings.session_ttl_seconds),
        )
    )
    response.set_cookie(
        "razbor_session",
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return await session_payload(db, user, token, settings)


@router.get("/auth/status")
async def auth_status(db: Db, request: Request):
    return {
        "bootstrap_required": await db.get(Setup, 1) is None,
        "bootstrap_token_required": bool(request.app.state.settings.bootstrap_token),
    }


@router.post("/auth/bootstrap", status_code=201)
async def bootstrap(body: Bootstrap, request: Request, response: Response, db: Db):
    expected = request.app.state.settings.bootstrap_token
    if expected and not hmac.compare_digest(body.bootstrap_token, expected):
        raise AppError("bootstrap_token", "Введите код первоначальной настройки из терминала", 403)
    if await db.get(Setup, 1) is not None:
        raise AppError("already_configured", "Первоначальная настройка уже завершена", 409)
    try:
        db.add(Setup(id=1))
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise AppError("already_configured", "Первоначальная настройка уже завершена", 409)
    user = User(
        id=new_id(),
        email=body.email,
        name=body.name,
        password_hash=await password_work(request, hash_password, body.password),
    )
    workspace = Workspace(id=new_id(), name=body.workspace_name)
    db.add_all([user, workspace])
    await db.flush()
    db.add(
        Membership(
            workspace_id=workspace.id,
            user_id=user.id,
            role="director",
            all_stores=True,
            data_access=True,
            owner=True,
        )
    )
    await db.flush()
    request.state.actor_id = user.id
    return await establish_session(db, user, request, response)


@router.post("/auth/login")
async def login(body: Login, request: Request, response: Response, db: Db):
    fingerprint = token_hash(body.email)
    address = token_hash("ip:" + (request.client.host if request.client else "unknown"))
    since = utcnow() - timedelta(minutes=15)
    attempts = await db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.fingerprint == fingerprint, LoginAttempt.attempted_at >= since)
    )
    if attempts >= 10:
        raise AppError("rate_limited", "Слишком много попыток. Повторите вход через 15 минут", 429)
    ip_attempts = await db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.fingerprint == address, LoginAttempt.attempted_at >= since)
    )
    if ip_attempts >= 30:
        raise AppError("rate_limited", "Слишком много попыток с этого адреса. Повторите позже", 429)
    user = await db.scalar(select(User).where(User.email == body.email))
    valid = await password_work(
        request,
        verify_password,
        body.password,
        user.password_hash if user else request.app.state.dummy_password_hash,
    )
    if not user or not user.active or not valid:
        db.add_all([LoginAttempt(fingerprint=fingerprint), LoginAttempt(fingerprint=address)])
        await db.commit()
        raise AppError("invalid_credentials", "Неверная почта или пароль", 401)
    await db.execute(delete(LoginAttempt).where(LoginAttempt.fingerprint == fingerprint))
    await db.execute(delete(Session).where(Session.expires_at < utcnow()))
    request.state.actor_id = user.id
    return await establish_session(db, user, request, response)


@router.get("/auth/session")
async def session(request: Request, user: CurrentUser, db: Db):
    return await session_payload(
        db, user, request.cookies["razbor_session"], request.app.state.settings
    )


@router.post("/auth/logout")
async def logout(request: Request, response: Response, user: CurrentUser, db: Db):
    await db.execute(
        delete(Session).where(Session.token_hash == token_hash(request.cookies["razbor_session"]))
    )
    response.delete_cookie(
        "razbor_session",
        path="/",
        secure=request.app.state.settings.secure_cookies,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


def member_payload(member, user):
    return {
        "id": member.id,
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "role": member.role,
        "all_stores": member.all_stores,
        "store_ids": member.store_ids,
        "active": member.active,
        "data_access": member.data_access,
        "owner": member.owner,
    }


async def validate_stores(db, workspace_id, store_ids):
    from backend.app.analytics.models import Store

    found = set(
        (
            await db.scalars(
                select(Store.id).where(Store.workspace_id == workspace_id, Store.id.in_(store_ids))
            )
        ).all()
    )
    if found != set(store_ids):
        raise AppError("invalid_scope", "Выбраны неизвестные точки")


@router.get("/workspaces/{workspace_id}/members")
async def members(access: WorkspaceAccess, db: Db):
    access.require("members:manage")
    records = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.workspace_id == access.workspace_id)
            .order_by(User.name)
        )
    ).all()
    return [member_payload(member, user) for member, user in records]


@router.post("/workspaces/{workspace_id}/members", status_code=201)
async def create_member(body: MemberCreate, access: WorkspaceAccess, db: Db, request: Request):
    access.require("members:manage")
    await validate_stores(db, access.workspace_id, body.store_ids)
    user = await db.scalar(select(User).where(User.email == body.email))
    if user:
        raise AppError(
            "email_exists",
            "Этот адрес уже используется. Для подключения существующего аккаунта требуется отдельное приглашение",
            409,
        )
    user = User(
        id=new_id(),
        email=body.email,
        name=body.name,
        password_hash=await password_work(request, hash_password, body.password),
    )
    db.add(user)
    await db.flush()
    member = Membership(
        workspace_id=access.workspace_id,
        user_id=user.id,
        role=body.role,
        all_stores=body.all_stores,
        store_ids=sorted(set(body.store_ids)),
        data_access=body.data_access,
    )
    db.add(member)
    await db.flush()
    return member_payload(member, user)


@router.patch("/workspaces/{workspace_id}/members/{member_id}")
async def update_member(member_id: str, body: MemberUpdate, access: WorkspaceAccess, db: Db):
    access.require("members:manage")
    member = await db.scalar(
        select(Membership)
        .where(Membership.id == member_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if not member or member.workspace_id != access.workspace_id:
        raise AppError("not_found", "Участник не найден", 404)
    if member.owner:
        raise AppError(
            "owner_protected", "Владелец пространства не может быть ограничен этим действием", 409
        )
    changes = body.model_dump(exclude_none=True)
    if "store_ids" in changes:
        await validate_stores(db, access.workspace_id, changes["store_ids"])
        changes["store_ids"] = sorted(set(changes["store_ids"]))
    for key, value in changes.items():
        setattr(member, key, value)
    member.revision = Membership.revision + 1
    await db.flush()
    await db.refresh(member)
    return member_payload(member, await db.get(User, member.user_id))


@router.get("/workspaces/{workspace_id}/participants")
async def participants(access: WorkspaceAccess, db: Db, store_ids: str = ""):
    access.require("cases:write")
    scope = [item for item in store_ids.split(",") if item]
    access.require_scope(scope)
    records = (
        await db.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.workspace_id == access.workspace_id,
                Membership.active.is_(True),
                User.active.is_(True),
            )
        )
    ).all()
    return [
        {"id": user.id, "name": user.name, "role": member.role}
        for member, user in records
        if member.data_access and (member.all_stores or set(scope).issubset(member.store_ids))
    ]
