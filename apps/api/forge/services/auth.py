"""AuthService - users, login, tokens, and team membership.

Multi-tenant model: a User belongs to exactly one Tenant (workspace) with a role
(owner > admin > editor > viewer). Self-service signup creates a new workspace +
owner; admins invite additional members into their workspace.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from forge.models import Tenant, User
from forge.models.entities import UserSecurity
from forge.security import (
    create_access_token,
    create_refresh_token,
    generate_totp_secret,
    hash_password,
    revoke_user_tokens,
    verify_password,
    verify_totp,
)

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
    async def users_by_email(session, email: str, *, tenant_id: str | None = None) -> list[User]:
        stmt = select(User).where(func.lower(User.email) == email.strip().lower())
        if tenant_id:
            stmt = stmt.where(User.tenant_id == tenant_id)
        return list((await session.execute(stmt.order_by(User.created_at))).scalars())

    @staticmethod
    async def get_by_email(session, email: str, *, tenant_id: str | None = None) -> User | None:
        """Return an unambiguous email match.

        Email is unique only inside a workspace. Callers that operate in a known workspace
        must pass ``tenant_id``; public flows without one get a result only when the address
        belongs to exactly one workspace, avoiding an arbitrary cross-tenant selection.
        """
        users = await AuthService.users_by_email(session, email, tenant_id=tenant_id)
        return users[0] if len(users) == 1 else None

    @staticmethod
    async def register(session, *, email: str, password: str, workspace_name: str | None = None) -> User:
        """Self-service signup: create a new workspace (tenant) + its owner user."""
        email = email.strip().lower()
        if not email or "@" not in email:
            raise AuthError("a valid email is required")
        if len(password) < 8:
            raise AuthError("password must be at least 8 characters")
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
    async def authenticate(
        session, *, email: str, password: str, tenant_id: str | None = None,
    ) -> User:
        users = await AuthService.users_by_email(session, email, tenant_id=tenant_id)
        matches = [u for u in users if verify_password(password, u.password_hash)]
        if not matches:
            raise AuthError("invalid email or password")
        if len(matches) > 1:
            raise AuthError("multiple workspaces use these credentials; provide workspace_id")
        user = matches[0]
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
        if await AuthService.get_by_email(session, email, tenant_id=tenant_id):
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

    # --- password reset / email verification (finding j) ---
    @staticmethod
    async def reset_password(session, *, user_id: str, tenant_id: str, password: str) -> User:
        """Redeem a password-reset token: set a new password for an ACTIVE account and sign out
        every existing session (logout-all) so a leaked-then-reset credential is fully cut off."""
        if len(password) < 8:
            raise AuthError("password must be at least 8 characters")
        user = await AuthService.get_user(session, user_id)
        if not user or user.tenant_id != tenant_id:
            raise AuthError("this reset link is no longer valid")
        if user.status == "disabled":
            raise AuthError("this account is disabled")
        user.password_hash = hash_password(password)
        if user.status == "invited":
            user.status = "active"
        await session.commit()
        await session.refresh(user)
        revoke_user_tokens(user.id)  # invalidate all outstanding access/refresh tokens
        return user

    @staticmethod
    async def _get_security(session, user_id: str) -> UserSecurity | None:
        return (
            await session.execute(select(UserSecurity).where(UserSecurity.user_id == user_id))
        ).scalar_one_or_none()

    @staticmethod
    async def _ensure_security(session, user: User) -> UserSecurity:
        row = await AuthService._get_security(session, user.id)
        if row is None:
            row = UserSecurity(tenant_id=user.tenant_id, user_id=user.id)
            session.add(row)
            await session.flush()
        return row

    @staticmethod
    async def mark_email_verified(session, *, user_id: str, tenant_id: str) -> User:
        user = await AuthService.get_user(session, user_id)
        if not user or user.tenant_id != tenant_id:
            raise AuthError("this verification link is no longer valid")
        row = await AuthService._ensure_security(session, user)
        row.email_verified = True
        row.email_verified_at = datetime.utcnow()
        await session.commit()
        return user

    @staticmethod
    async def email_verified(session, user_id: str) -> bool:
        row = await AuthService._get_security(session, user_id)
        return bool(row and row.email_verified)

    # --- optional TOTP MFA (finding j) ---
    @staticmethod
    async def totp_status(session, user_id: str) -> bool:
        row = await AuthService._get_security(session, user_id)
        return bool(row and row.totp_enabled)

    @staticmethod
    async def enroll_totp(session, user: User) -> str:
        """Generate + store a fresh TOTP secret (NOT yet enabled - confirm with a code first).
        Returns the base32 secret so the caller can render an otpauth QR."""
        row = await AuthService._ensure_security(session, user)
        secret = generate_totp_secret()
        row.totp_secret = secret
        row.totp_enabled = False
        await session.commit()
        return secret

    @staticmethod
    async def confirm_totp(session, *, user: User, code: str) -> bool:
        """Verify the first code against the enrolled secret and, on success, enable MFA."""
        row = await AuthService._get_security(session, user.id)
        if row is None or not row.totp_secret or not verify_totp(row.totp_secret, code):
            return False
        row.totp_enabled = True
        await session.commit()
        return True

    @staticmethod
    async def disable_totp(session, user: User) -> None:
        row = await AuthService._get_security(session, user.id)
        if row is not None:
            row.totp_secret = None
            row.totp_enabled = False
            await session.commit()

    @staticmethod
    async def check_login_totp(session, user: User, code: str | None) -> bool:
        """True if the user has no MFA, or MFA is on and `code` is valid."""
        row = await AuthService._get_security(session, user.id)
        if not (row and row.totp_enabled and row.totp_secret):
            return True
        return verify_totp(row.totp_secret, code)

    # --- workspace (tenant) administration (finding k) ---
    @staticmethod
    async def update_workspace(
        session, *, tenant_id: str, name: str | None = None, plan: str | None = None,
        settings_patch: dict | None = None,
    ) -> Tenant:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is None:
            raise AuthError("workspace not found")
        if name is not None:
            tenant.name = name
        if plan is not None:
            tenant.plan = plan
        if settings_patch is not None:
            merged = dict(tenant.settings or {})
            merged.update(settings_patch)
            tenant.settings = merged
        await session.commit()
        await session.refresh(tenant)
        return tenant

    @staticmethod
    async def delete_workspace(session, *, tenant_id: str, checkpointer=None) -> None:
        """Guarded, cascading workspace deletion (finding k). Deletes every project (and its
        runtime/vector/checkpoint artifacts) then all tenant-scoped auth rows and the tenant.
        The caller MUST enforce owner-only + an explicit name confirmation before calling this."""
        from sqlalchemy import delete as sa_delete

        from forge.models import AuditLog, Project
        from forge.models.entities import ApiKey, ProjectMember
        from forge.services.projects import ProjectService

        projects = (
            await session.execute(select(Project).where(Project.tenant_id == tenant_id))
        ).scalars().all()
        for project in projects:
            await ProjectService.delete(session, project, checkpointer=checkpointer)
        for model in (ProjectMember, ApiKey, UserSecurity, AuditLog, User):
            await session.execute(sa_delete(model).where(model.tenant_id == tenant_id))
        tenant = await session.get(Tenant, tenant_id)
        if tenant is not None:
            await session.delete(tenant)
        await session.commit()
