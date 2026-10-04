from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.access.models import Membership, User
from backend.app.infrastructure.errors import AppError


COMMON = {"analytics:read", "assistant:use", "reports:write", "cases:write"}
CAPABILITIES = {
    "director": COMMON | {"plans:write"},
    "regional_manager": COMMON | {"plans:write"},
    "franchise_owner": COMMON,
    "store_manager": COMMON,
    "analyst": COMMON | {"metrics:write"},
    "admin": {"sources:manage", "members:manage"},
}


@dataclass(frozen=True)
class Access:
    user_id: str
    workspace_id: str
    membership_id: str
    revision: int
    role: str
    all_stores: bool
    store_ids: frozenset[str]
    capabilities: frozenset[str]
    owner: bool

    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise AppError("forbidden", "Недостаточно прав для этого действия", 403)

    def require_scope(self, store_ids: list[str], *, allow_empty: bool = False) -> None:
        self.require("analytics:read")
        if not store_ids and not allow_empty:
            raise AppError("empty_scope", "Выберите хотя бы одну доступную точку", 400)
        if not self.all_stores and not set(store_ids).issubset(self.store_ids):
            raise AppError("scope_forbidden", "Область данных недоступна", 403)


def from_membership(member: Membership) -> Access:
    capabilities = set(CAPABILITIES[member.role])
    if member.owner:
        capabilities.update({"sources:manage", "members:manage", "metrics:write", "plans:write"})
    if not member.data_access:
        capabilities.difference_update(COMMON | {"plans:write", "metrics:write"})
    elif member.role == "admin":
        capabilities.update(COMMON)
    return Access(
        member.user_id,
        member.workspace_id,
        member.id,
        member.revision,
        member.role,
        member.all_stores,
        frozenset(member.store_ids),
        frozenset(capabilities),
        member.owner,
    )


async def get_access(db: AsyncSession, user_id: str, workspace_id: str) -> Access:
    member = await db.scalar(
        select(Membership)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.user_id == user_id,
            Membership.workspace_id == workspace_id,
            Membership.active.is_(True),
            User.active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    if member is None:
        raise AppError("not_found", "Пространство недоступно", 404)
    return from_membership(member)
