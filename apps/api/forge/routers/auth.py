"""Auth + team-management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from forge.config import settings
from forge.deps import (
    CurrentUser,
    client_ip,
    current_tenant_id,
    get_current_user,
    get_session,
    require_role,
)
from forge.security import TokenError, create_invite_token, decode_token
from forge.services.audit import AuditService
from forge.services.auth import AuthError, AuthService
from forge.util.mailer import send_email

router = APIRouter(prefix="/v1/auth", tags=["auth"])
team_router = APIRouter(prefix="/v1/team", tags=["team"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    workspace_name: str | None = None


class LoginIn(BaseModel):
    # plain str (not EmailStr): login accepts whatever was registered, incl. local addresses.
    email: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class InviteIn(BaseModel):
    email: EmailStr
    role: str = "editor"
    password: str | None = Field(default=None, min_length=8)


class AcceptInviteIn(BaseModel):
    token: str
    password: str = Field(min_length=8)


def _invite_link(token: str) -> str:
    return f"{settings.public_console_url.rstrip('/')}/?invite={token}"


async def _send_invite_email(*, to: str, link: str, inviter: str | None, role: str) -> bool:
    who = f"{inviter} " if inviter else ""
    subject = "You've been invited to Forge"
    body = (
        f"{who}invited you to join their Forge workspace as a {role}.\n\n"
        f"Set your password and get started:\n{link}\n\n"
        "This link expires in 7 days. If you weren't expecting this, you can ignore this email."
    )
    html = (
        f"<p>{who}invited you to join their Forge workspace as a <b>{role}</b>.</p>"
        f'<p><a href="{link}">Set your password and get started →</a></p>'
        "<p style='color:#888;font-size:13px'>This link expires in 7 days. "
        "If you weren't expecting this, you can ignore this email.</p>"
    )
    return await send_email(to=to, subject=subject, body=body, html=html)


class MemberPatch(BaseModel):
    role: str | None = None
    status: str | None = None


class PasswordIn(BaseModel):
    password: str = Field(min_length=8)


def _user_out(u) -> dict:
    return {"id": u.id, "email": u.email, "role": u.role, "status": u.status, "tenant_id": u.tenant_id}


@router.post("/register", status_code=201)
async def register(body: RegisterIn, request: Request, session: AsyncSession = Depends(get_session)):
    if not settings.allow_open_signup:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "open signup is disabled; ask an admin for an invite")
    try:
        user = await AuthService.register(
            session, email=str(body.email), password=body.password, workspace_name=body.workspace_name
        )
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await AuditService.log(tenant_id=user.tenant_id, action="auth.register", actor_id=user.id,
                           actor_email=user.email, ip=client_ip(request))
    return {**AuthService.tokens_for(user), "user": _user_out(user)}


@router.post("/login")
async def login(body: LoginIn, request: Request, session: AsyncSession = Depends(get_session)):
    try:
        user = await AuthService.authenticate(session, email=str(body.email), password=body.password)
    except AuthError as e:
        await AuditService.log(tenant_id="-", action="auth.login", actor_email=str(body.email),
                               ip=client_ip(request), status="denied", meta={"reason": str(e)})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    await AuditService.log(tenant_id=user.tenant_id, action="auth.login", actor_id=user.id,
                           actor_email=user.email, ip=client_ip(request))
    return {**AuthService.tokens_for(user), "user": _user_out(user)}


@router.post("/refresh")
async def refresh(body: RefreshIn, session: AsyncSession = Depends(get_session)):
    try:
        claims = decode_token(body.refresh_token, expected_type="refresh")
    except TokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    user = await AuthService.get_user(session, claims.get("sub", ""))
    if user is None or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account not found or disabled")
    return AuthService.tokens_for(user)


@router.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)):
    return {"id": user.id, "email": user.email, "role": user.role, "tenant_id": user.tenant_id,
            "is_fallback": user.is_fallback}


@router.post("/set-password")
async def set_my_password(body: PasswordIn, user: CurrentUser = Depends(get_current_user),
                          session: AsyncSession = Depends(get_session)):
    if user.is_fallback:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot set a password for the fallback dev user")
    try:
        await AuthService.set_password(session, user_id=user.id, password=body.password)
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"ok": True}


# --- team management (admin+) ---
@team_router.get("/members")
async def list_members(session: AsyncSession = Depends(get_session),
                       tenant_id: str = Depends(current_tenant_id),
                       _: CurrentUser = Depends(require_role("admin"))):
    return [_user_out(u) for u in await AuthService.list_members(session, tenant_id)]


@team_router.post("/members", status_code=201)
async def invite_member(body: InviteIn, request: Request, session: AsyncSession = Depends(get_session),
                        admin: CurrentUser = Depends(require_role("admin"))):
    try:
        user = await AuthService.invite(session, tenant_id=admin.tenant_id, email=str(body.email),
                                        role=body.role, password=body.password)
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await AuditService.log(tenant_id=admin.tenant_id, action="team.invite", actor_id=admin.id,
                           actor_email=admin.email, resource_type="user", resource_id=user.id,
                           ip=client_ip(request), meta={"email": user.email, "role": user.role})

    out = _user_out(user)
    # No password set => emailed-invite flow: mint a redeemable link and try to send it.
    # If SMTP isn't configured, hand the link back so the admin can share it manually.
    if user.status == "invited":
        token = create_invite_token(user_id=user.id, tenant_id=user.tenant_id)
        link = _invite_link(token)
        out["email_sent"] = await _send_invite_email(to=user.email, link=link, inviter=admin.email, role=user.role)
        if not out["email_sent"]:
            out["invite_url"] = link
    else:
        out["email_sent"] = False  # admin set a temp password; share it out-of-band
    return out


@router.get("/invite-info")
async def invite_info(token: str, session: AsyncSession = Depends(get_session)):
    """Public: validate an invite token and return who it's for (for the accept screen)."""
    try:
        claims = decode_token(token, expected_type="invite")
    except TokenError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this invite link is invalid or has expired") from e
    user = await AuthService.get_user(session, claims.get("sub", ""))
    if user is None or user.status != "invited":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this invite has already been used or was revoked")
    return {"email": user.email, "role": user.role}


@router.post("/accept-invite")
async def accept_invite(body: AcceptInviteIn, request: Request, session: AsyncSession = Depends(get_session)):
    """Public: redeem an invite token, set the first password, and log the user in."""
    try:
        claims = decode_token(body.token, expected_type="invite")
    except TokenError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this invite link is invalid or has expired") from e
    try:
        user = await AuthService.accept_invite(
            session, user_id=claims.get("sub", ""), tenant_id=claims.get("tid", ""), password=body.password
        )
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await AuditService.log(tenant_id=user.tenant_id, action="auth.accept_invite", actor_id=user.id,
                           actor_email=user.email, ip=client_ip(request))
    return {**AuthService.tokens_for(user), "user": _user_out(user)}


@team_router.patch("/members/{user_id}")
async def update_member(user_id: str, body: MemberPatch, request: Request,
                        session: AsyncSession = Depends(get_session),
                        admin: CurrentUser = Depends(require_role("admin"))):
    try:
        user = None
        if body.role is not None:
            user = await AuthService.set_role(session, tenant_id=admin.tenant_id, user_id=user_id, role=body.role)
        if body.status is not None:
            user = await AuthService.set_status(session, tenant_id=admin.tenant_id, user_id=user_id, status=body.status)
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "nothing to update")
    await AuditService.log(tenant_id=admin.tenant_id, action="team.update", actor_id=admin.id,
                           actor_email=admin.email, resource_type="user", resource_id=user_id,
                           ip=client_ip(request), meta=body.model_dump(exclude_none=True))
    return _user_out(user)


@team_router.delete("/members/{user_id}")
async def deactivate_member(user_id: str, request: Request, session: AsyncSession = Depends(get_session),
                            admin: CurrentUser = Depends(require_role("admin"))):
    if user_id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "you cannot deactivate yourself")
    try:
        await AuthService.set_status(session, tenant_id=admin.tenant_id, user_id=user_id, status="disabled")
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await AuditService.log(tenant_id=admin.tenant_id, action="team.deactivate", actor_id=admin.id,
                           actor_email=admin.email, resource_type="user", resource_id=user_id, ip=client_ip(request))
    return {"ok": True}
