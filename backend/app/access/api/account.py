from datetime import timedelta
import secrets

from fastapi import APIRouter, Request, Response
from pydantic import Field, field_validator, model_validator
from sqlalchemy import delete, select, update

from backend.app.access.dependencies import CurrentUser, Db, WorkspaceAccess, current_user
from backend.app.access.models import Invitation, Membership, Session, User, Workspace
from backend.app.access.policy import get_access
from backend.app.access.api.auth import establish_session, session_payload, validate_stores
from backend.app.access.api.schemas import NewPassword, Password, Role
from backend.app.infrastructure.database import aware, new_id, utcnow
from backend.app.infrastructure.errors import AppError
from backend.app.infrastructure.schemas import Input
from backend.app.infrastructure.security import (
    hash_password,
    password_work,
    token_hash,
    verify_password,
)


router = APIRouter()


class PasswordChange(Input):
    current_password: Password
    new_password: NewPassword


class WorkspaceCreate(Input):
    name: str = Field(min_length=1, max_length=160)


class InvitationCreate(Input):
    email: str = Field(min_length=3, max_length=254)
    role: Role
    all_stores: bool = False
    store_ids: list[str] = Field(default_factory=list, max_length=1000)
    data_access: bool = True

    @model_validator(mode="after")
    def technical_admin(self):
        if self.role == "admin" and "data_access" not in self.model_fields_set:
            self.data_access = False
        return self

    @field_validator("email")
    @classmethod
    def email_address(cls, value):
        if value.count("@") != 1 or " " in value:
            raise ValueError("Enter a valid email")
        return value.lower()


class InvitationAccept(Input):
    token: str = Field(min_length=20, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    password: NewPassword | None = None


@router.post("/auth/password")
async def change_password(
    body: PasswordChange, request: Request, response: Response, user: CurrentUser, db: Db
):
    locked = await db.scalar(
        select(User)
        .where(User.id == user.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if not await password_work(
        request, verify_password, body.current_password, locked.password_hash
    ):
        raise AppError("invalid_password", "Текущий пароль неверен", 400)
    locked.password_hash = await password_work(request, hash_password, body.new_password)
    await db.execute(delete(Session).where(Session.user_id == user.id))
    return await establish_session(db, locked, request, response)


@router.post("/auth/sessions/revoke")
async def revoke_sessions(request: Request, user: CurrentUser, db: Db):
    current = token_hash(request.cookies["razbor_session"])
    await db.execute(
        delete(Session).where(Session.user_id == user.id, Session.token_hash != current)
    )
    return {"ok": True}


@router.post("/workspaces", status_code=201)
async def create_workspace(body: WorkspaceCreate, user: CurrentUser, db: Db):
    workspace = Workspace(id=new_id(), name=body.name)
    db.add(workspace)
    await db.flush()
    member = Membership(
        workspace_id=workspace.id,
        user_id=user.id,
        role="director",
        all_stores=True,
        data_access=True,
        owner=True,
    )
    db.add(member)
    await db.flush()
    from backend.app.access.policy import from_membership

    return {
        "id": workspace.id,
        "name": workspace.name,
        "role": member.role,
        "capabilities": sorted(from_membership(member).capabilities),
        "all_stores": True,
        "store_ids": [],
    }


@router.post("/workspaces/{workspace_id}/invitations", status_code=201)
async def invite(body: InvitationCreate, access: WorkspaceAccess, db: Db):
    access.require("members:manage")
    await validate_stores(db, access.workspace_id, body.store_ids)
    user = await db.scalar(select(User).where(User.email == body.email))
    if user and await db.scalar(
        select(Membership.id).where(
            Membership.workspace_id == access.workspace_id, Membership.user_id == user.id
        )
    ):
        raise AppError("already_member", "Участник уже добавлен в пространство", 409)
    await db.execute(
        update(Invitation)
        .where(
            Invitation.workspace_id == access.workspace_id,
            Invitation.email == body.email,
            Invitation.consumed_at.is_(None),
        )
        .values(consumed_at=utcnow())
    )
    token = secrets.token_urlsafe(40)
    item = Invitation(
        workspace_id=access.workspace_id,
        created_by=access.user_id,
        token_hash=token_hash(token),
        expires_at=utcnow() + timedelta(days=7),
        **body.model_dump(),
    )
    db.add(item)
    await db.flush()
    return {"id": item.id, "email": item.email, "expires_at": item.expires_at, "token": token}


@router.get("/workspaces/{workspace_id}/invitations")
async def invitations(access: WorkspaceAccess, db: Db):
    access.require("members:manage")
    records = (
        await db.scalars(
            select(Invitation)
            .where(
                Invitation.workspace_id == access.workspace_id,
                Invitation.consumed_at.is_(None),
                Invitation.expires_at > utcnow(),
            )
            .order_by(Invitation.created_at.desc())
        )
    ).all()
    return [
        {"id": item.id, "email": item.email, "role": item.role, "expires_at": item.expires_at}
        for item in records
    ]


@router.post("/workspaces/{workspace_id}/invitations/{invitation_id}/revoke")
async def revoke_invitation(invitation_id: str, access: WorkspaceAccess, db: Db):
    access.require("members:manage")
    item = await db.get(Invitation, invitation_id)
    if not item or item.workspace_id != access.workspace_id:
        raise AppError("not_found", "Приглашение не найдено", 404)
    item.consumed_at = utcnow()
    return {"ok": True}


@router.post("/auth/invitations/accept")
async def accept_invitation(body: InvitationAccept, request: Request, response: Response, db: Db):
    invitation = await db.scalar(
        select(Invitation).where(Invitation.token_hash == token_hash(body.token)).with_for_update()
    )
    if (
        invitation is None
        or invitation.consumed_at is not None
        or aware(invitation.expires_at) <= utcnow()
    ):
        raise AppError("invalid_invitation", "Приглашение недействительно или истекло", 400)
    issuer = await get_access(db, invitation.created_by, invitation.workspace_id)
    issuer.require("members:manage")
    await validate_stores(db, invitation.workspace_id, invitation.store_ids)
    existing = await db.scalar(select(User).where(User.email == invitation.email))
    if existing:
        user = await current_user(request, db)
        if user.id != existing.id:
            raise AppError(
                "invitation_account", "Войдите под адресом, для которого создано приглашение", 403
            )
    else:
        if not body.name or not body.password:
            raise AppError("registration_required", "Укажите имя и пароль для нового аккаунта", 422)
        if request.cookies.get("razbor_session"):
            raise AppError(
                "logout_required", "Выйдите из текущего аккаунта перед регистрацией нового", 409
            )
        user = User(
            id=new_id(),
            email=invitation.email,
            name=body.name,
            password_hash=await password_work(request, hash_password, body.password),
        )
        db.add(user)
        await db.flush()
    consumed = await db.execute(
        update(Invitation)
        .where(
            Invitation.id == invitation.id,
            Invitation.consumed_at.is_(None),
            Invitation.expires_at > utcnow(),
        )
        .values(consumed_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        raise AppError("invalid_invitation", "Приглашение уже использовано", 409)
    member = Membership(
        workspace_id=invitation.workspace_id,
        user_id=user.id,
        role=invitation.role,
        all_stores=invitation.all_stores,
        store_ids=invitation.store_ids,
        data_access=invitation.data_access,
    )
    db.add(member)
    await db.flush()
    request.state.actor_id = user.id
    if existing:
        return await session_payload(
            db, user, request.cookies["razbor_session"], request.app.state.settings
        )
    return await establish_session(db, user, request, response)
