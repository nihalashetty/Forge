"""AuthService - users, login, tokens, and team membership.

Multi-tenant model: a User belongs to exactly one Tenant (workspace) with a role
(owner > admin > editor > viewer). Self-service signup creates a new workspace +
owner; admins invite additional members into their workspace.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from forge.models import Tenant, User
from forge.security import create_access_token, create_refresh_token, hash_password, verify_password

ROLES = ("owner", "admin", "editor", "viewer")
_RANK = {r: i for i, r in enumerate(ROLES)}  # higher index = fewer privileges


class AuthError(ValueError):
    """Login/registration failure (bad credentials, duplicate email, etc.)."""


def role_at_least(role: str, minimum: str) -> bool:
    """True if `role` is at least as privileged as `minimum` (owner is most)."""
    return _RANK.get(role, 99) <= _RANK.get(minimum, -1)


class AuthService:
    @staticmethod
    async def get_user(session, user_id: str) -> User | None:
        return (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()

    @staticmethod
    async def get_by_email(session, email: str) -> User | None:
        return (
            await session.execute(select(User).where(func.lower(User.email) == email.strip().lower()))
        ).scalar_one_or_none()

    @staticmethod
    async def register(session, *, email: str, password: str, workspace_name: str | None = None) -> User:
        """Self-service signup: create a new workspace (tenant) + its owner user."""
        email = email.strip().lower()
        if not email or "@" not in email:
            raise AuthError("a valid email is required")
        if len(password) < 8:
            raise AuthError("password must be at least 8 characters")
        if await AuthService.get_by_email(session, email):
            raise AuthError("an account with that email already exists")
        tenant = Tenant(name=workspace_name or f"{email.split('@')[0]}'s workspace", plan="free")
        session.add(tenant)
        await session.flush()
        user = User(
            tenant_id=tenant.id, email=email, password_hash=hash_password(password),
            role="owner", status="active",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def authenticate(session, *, email: str, password: str) -> User:
        user = await AuthService.get_by_email(session, email)
        if not user or not verify_password(password, user.password_hash):
            raise AuthError("invalid email or password")
        if user.status != "active":
            raise AuthError("this account is disabled")
        user.last_login_at = datetime.utcnow()
        await session.commit()
        return user

    @staticmethod
    def tokens_for(user: User) -> dict:
        return {
            "access_token": create_access_token(user_id=user.id, tenant_id=user.tenant_id, role=user.role),
            "refresh_token": create_refresh_token(user_id=user.id, tenant_id=user.tenant_id),
            "token_type": "bearer",
        }

    # --- team management ---
    @staticmethod
    async def list_members(session, tenant_id: str) -> list[User]:
        rows = await session.execute(
            select(User).where(User.tenant_id == tenant_id).order_by(User.created_at)
        )
        return list(rows.scalars())

    @staticmethod
    async def invite(session, *, tenant_id: str, email: str, role: str = "editor", password: str | None = None) -> User:
        email = email.strip().lower()
        if role not in ROLES:
            raise AuthError(f"unknown role {role!r}")
        if await AuthService.get_by_email(session, email):
            raise AuthError("a user with that email already exists")
        user = User(
            tenant_id=tenant_id, email=email, role=role,
            password_hash=hash_password(password) if password else None,
            status="active" if password else "invited",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def set_role(session, *, tenant_id: str, user_id: str, role: str) -> User:
        if role not in ROLES:
            raise AuthError(f"unknown role {role!r}")
        user = await AuthService.get_user(session, user_id)
        if not user or user.tenant_id != tenant_id:
            raise AuthError("user not found in this workspace")
        # Never strip the last owner of their role (would orphan the workspace).
        if user.role == "owner" and role != "owner":
            owners = await session.execute(
                select(func.count()).select_from(User).where(
                    User.tenant_id == tenant_id, User.role == "owner", User.status == "active"
                )
            )
            if (owners.scalar() or 0) <= 1:
                raise AuthError("cannot demote the only owner")
        user.role = role
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def set_status(session, *, tenant_id: str, user_id: str, status: str) -> User:
        user = await AuthService.get_user(session, user_id)
        if not user or user.tenant_id != tenant_id:
            raise AuthError("user not found in this workspace")
        user.status = status
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def set_password(session, *, user_id: str, password: str) -> User:
        if len(password) < 8:
            raise AuthError("password must be at least 8 characters")
        user = await AuthService.get_user(session, user_id)
        if not user:
            raise AuthError("user not found")
        user.password_hash = hash_password(password)
        if user.status == "invited":
            user.status = "active"
        await session.commit()
        await session.refresh(user)
        return user

    @staticmethod
    async def accept_invite(session, *, user_id: str, tenant_id: str, password: str) -> User:
        """Redeem an invite token: set the invitee's first password and activate them.
        Only a still-pending ('invited') user may be redeemed - so a leaked link can never
        reset an already-active account's password."""
        if len(password) < 8:
            raise AuthError("password must be at least 8 characters")
        user = await AuthService.get_user(session, user_id)
        if not user or user.tenant_id != tenant_id:
            raise AuthError("this invite is no longer valid")
        if user.status != "invited":
            raise AuthError("this invite has already been used")
        user.password_hash = hash_password(password)
        user.status = "active"
        await session.commit()
        await session.refresh(user)
        return user
