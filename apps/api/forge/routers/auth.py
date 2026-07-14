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
from forge.security import (
    TokenError,
    create_email_verification_token,
    create_invite_token,
    create_password_reset_token,
    decode_token,
    is_revoked,
    revoke,
    revoke_user_tokens,
    tokens_revoked_after,
    totp_provisioning_uri,
)
from forge.services.audit import AuditService
from forge.services.auth import AuthError, AuthService
from forge.util.mailer import send_email
from forge.util.ratelimit import rate_limiter

router = APIRouter(prefix="/v1/auth", tags=["auth"])
team_router = APIRouter(prefix="/v1/team", tags=["team"])
workspace_router = APIRouter(prefix="/v1/workspace", tags=["workspace"])
apikeys_router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])


def _auth_throttle(request: Request, email: str | None = None) -> None:
    """Ceiling on the unauthenticated auth endpoints - brute-force / credential-stuffing guard
    (finding a). Two dimensions: a STRICT per-email bucket (per-account brute-force) and a LOOSER
    per-IP bucket at 10x (credential-stuffing / DoS across many accounts). 429 when either is
    empty. `auth_rate_limit_per_minute` is the per-email rate; 0 disables both."""
    rate = settings.auth_rate_limit_per_minute
    if rate <= 0:
        return
    ip = client_ip(request) or "unknown"
    checks = [(f"auth:ip:{ip}", rate * 10)]
    if email:
        checks.append((f"auth:email:{email.strip().lower()}", rate))
    for key, per_min in checks:
        if not rate_limiter.allow(key, rate=per_min, per=60):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts; please slow down and try again")


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    workspace_name: str | None = None


class LoginIn(BaseModel):
    # plain str (not EmailStr): login accepts whatever was registered, incl. local addresses.
    email: str
    password: str
    totp_code: str | None = None  # required only when the account has TOTP MFA enabled


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    # Optional: the refresh token to revoke. Unauthenticated (possession of the signed token is
    # itself the proof) so it works even after the access token has expired.
    refresh_token: str | None = None


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
    _auth_throttle(request, str(body.email))
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
    _auth_throttle(request, str(body.email))
    try:
        user = await AuthService.authenticate(session, email=str(body.email), password=body.password)
    except AuthError as e:
        await AuditService.log(tenant_id="-", action="auth.login", actor_email=str(body.email),
                               ip=client_ip(request), status="denied", meta={"reason": str(e)})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    # Second factor (only enforced when the account has TOTP enabled).
    if not await AuthService.check_login_totp(session, user, body.totp_code):
        await AuditService.log(tenant_id=user.tenant_id, action="auth.login", actor_id=user.id,
                               actor_email=user.email, ip=client_ip(request), status="denied",
                               meta={"reason": "totp_required_or_invalid"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "a valid authenticator code is required", {"WWW-Authenticate": "TOTP"})
    await AuditService.log(tenant_id=user.tenant_id, action="auth.login", actor_id=user.id,
                           actor_email=user.email, ip=client_ip(request))
    return {**AuthService.tokens_for(user), "user": _user_out(user)}


@router.post("/refresh")
async def refresh(body: RefreshIn, request: Request, session: AsyncSession = Depends(get_session)):
    _auth_throttle(request)
    # Decode WITHOUT the revoked-check so a reused (already-rotated) refresh token is detected
    # rather than looking like a plain invalid token (finding d).
    try:
        claims = decode_token(body.refresh_token, expected_type="refresh", check_revoked=False)
    except TokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    uid = claims.get("sub", "")
    # Reuse detection: a rotated refresh token presented again is a theft signal -> sign the
    # whole user out (revoke every session) and refuse.
    if is_revoked(claims.get("jti")):
        revoke_user_tokens(uid)
        await AuditService.log(tenant_id=claims.get("tid", "-"), action="auth.refresh_reuse",
                               actor_id=uid, ip=client_ip(request), status="denied")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "refresh token reuse detected; all sessions signed out")
    if tokens_revoked_after(claims):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session has been signed out")
    user = await AuthService.get_user(session, uid)
    if user is None or user.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account not found or disabled")
    # Rotate: denylist the presented refresh jti (self-expiring at its own exp) and mint a fresh
    # access + refresh pair.
    revoke(claims.get("jti"), exp=claims.get("exp"))
    return AuthService.tokens_for(user)


@router.post("/logout")
async def logout(body: LogoutIn):
    """Revoke the presented refresh token (this device/session). Unauthenticated: possession of
    the signed token is the proof, so it works even after the access token expires (finding d)."""
    if body.refresh_token:
        try:
            claims = decode_token(body.refresh_token, expected_type="refresh", check_revoked=False)
            revoke(claims.get("jti"), exp=claims.get("exp"))
        except TokenError:
            pass  # already invalid/expired - nothing to revoke
    return {"ok": True}


@router.post("/logout-all")
async def logout_all(request: Request, user: CurrentUser = Depends(get_current_user)):
    """Sign out every session for the current user (all devices) by advancing their revocation
    cutoff so all previously-issued access/refresh tokens are rejected (finding d)."""
    revoke_user_tokens(user.id)
    await AuditService.log(tenant_id=user.tenant_id, action="auth.logout_all", actor_id=user.id,
                           actor_email=user.email, ip=client_ip(request))
    return {"ok": True}


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
    _auth_throttle(request)
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


# --- password reset (finding j) ---
class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetIn(BaseModel):
    token: str
    password: str = Field(min_length=8)


async def _send_link_email(*, to: str, subject: str, intro: str, link: str, expires: str) -> bool:
    body = f"{intro}\n\n{link}\n\nThis link expires in {expires}. If you weren't expecting this, ignore this email."
    html = (f"<p>{intro}</p><p><a href=\"{link}\">Continue →</a></p>"
            f"<p style='color:#888;font-size:13px'>This link expires in {expires}. "
            "If you weren't expecting this, you can ignore this email.</p>")
    return await send_email(to=to, subject=subject, body=body, html=html)


@router.post("/request-password-reset")
async def request_password_reset(body: PasswordResetRequestIn, request: Request,
                                 session: AsyncSession = Depends(get_session)):
    """Public: email a signed reset link. Always returns ok (never reveals whether the address
    exists). No SMTP configured => the link is returned so an admin/dev can use it."""
    _auth_throttle(request, str(body.email))
    user = await AuthService.get_by_email(session, str(body.email))
    if user and user.status != "disabled":
        token = create_password_reset_token(user_id=user.id, tenant_id=user.tenant_id)
        link = f"{settings.public_console_url.rstrip('/')}/?reset={token}"
        sent = await _send_link_email(to=user.email, subject="Reset your Forge password",
                                      intro="Use the link below to set a new password.",
                                      link=link, expires="1 hour")
        await AuditService.log(tenant_id=user.tenant_id, action="auth.request_password_reset",
                               actor_id=user.id, actor_email=user.email, ip=client_ip(request))
        if not sent:
            return {"ok": True, "reset_url": link}
    return {"ok": True}


@router.post("/reset-password")
async def reset_password(body: PasswordResetIn, request: Request,
                         session: AsyncSession = Depends(get_session)):
    """Public: redeem a reset token, set a new password, and sign out all existing sessions.
    Does NOT auto-login (user re-authenticates, re-prompting MFA)."""
    _auth_throttle(request)
    try:
        claims = decode_token(body.token, expected_type="pwreset")
    except TokenError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this reset link is invalid or has expired") from e
    try:
        user = await AuthService.reset_password(
            session, user_id=claims.get("sub", ""), tenant_id=claims.get("tid", ""), password=body.password
        )
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    revoke(claims.get("jti"), exp=claims.get("exp"))  # one-time use
    await AuditService.log(tenant_id=user.tenant_id, action="auth.reset_password", actor_id=user.id,
                           actor_email=user.email, ip=client_ip(request))
    return {"ok": True}


# --- email verification (finding j) ---
class TokenIn(BaseModel):
    token: str


@router.post("/request-email-verification")
async def request_email_verification(request: Request, user: CurrentUser = Depends(get_current_user)):
    if user.is_fallback or not user.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no verifiable email for this account")
    token = create_email_verification_token(user_id=user.id, tenant_id=user.tenant_id)
    link = f"{settings.public_console_url.rstrip('/')}/?verify_email={token}"
    sent = await _send_link_email(to=user.email, subject="Verify your Forge email",
                                  intro="Confirm this email address for your Forge account.",
                                  link=link, expires="3 days")
    return {"ok": True} if sent else {"ok": True, "verify_url": link}


@router.post("/verify-email")
async def verify_email(body: TokenIn, session: AsyncSession = Depends(get_session)):
    try:
        claims = decode_token(body.token, expected_type="email_verify")
    except TokenError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this verification link is invalid or has expired") from e
    try:
        await AuthService.mark_email_verified(session, user_id=claims.get("sub", ""), tenant_id=claims.get("tid", ""))
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    revoke(claims.get("jti"), exp=claims.get("exp"))
    return {"ok": True}


# --- TOTP MFA (finding j; optional per user) ---
class TotpCodeIn(BaseModel):
    code: str


@router.post("/mfa/totp/enroll")
async def totp_enroll(user: CurrentUser = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    """Generate a TOTP secret (NOT yet active - confirm a code to enable). Returns the secret
    and an otpauth:// URL for an authenticator-app QR."""
    if user.is_fallback:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot enroll MFA for the dev fallback user")
    u = await AuthService.get_user(session, user.id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    secret = await AuthService.enroll_totp(session, u)
    return {"secret": secret, "otpauth_url": totp_provisioning_uri(secret, account=u.email, issuer=settings.app_name)}


@router.post("/mfa/totp/confirm")
async def totp_confirm(body: TotpCodeIn, user: CurrentUser = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    u = await AuthService.get_user(session, user.id)
    if u is None or not await AuthService.confirm_totp(session, user=u, code=body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid authenticator code")
    return {"ok": True, "mfa_enabled": True}


@router.post("/mfa/totp/disable")
async def totp_disable(body: TotpCodeIn, user: CurrentUser = Depends(get_current_user),
                       session: AsyncSession = Depends(get_session)):
    """Disable MFA. Requires a current code so a hijacked session can't silently turn it off."""
    u = await AuthService.get_user(session, user.id)
    if u is None or not await AuthService.check_login_totp(session, u, body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid authenticator code")
    await AuthService.disable_totp(session, u)
    return {"ok": True, "mfa_enabled": False}


# --- workspace (tenant) administration (finding k) ---
class WorkspaceUpdateIn(BaseModel):
    name: str | None = None
    plan: str | None = None
    # Quota / limit overrides merged into tenant.settings (max_runs_per_day, max_cost_per_day_usd,
    # max_tokens_per_day, project_limits, reset_tz, ...). Merged, not replaced.
    settings: dict | None = None


class WorkspaceDeleteIn(BaseModel):
    # Must equal the workspace name - a deliberate, un-fat-fingerable confirmation for a
    # destructive, irreversible cascade.
    confirm_name: str


def _tenant_out(t) -> dict:
    return {"id": t.id, "name": t.name, "plan": t.plan, "settings": t.settings or {}}


@workspace_router.get("")
async def get_workspace(session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id),
                        _: CurrentUser = Depends(require_role("admin"))):
    from forge.models import Tenant

    t = await session.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not found")
    return _tenant_out(t)


@workspace_router.patch("")
async def update_workspace(body: WorkspaceUpdateIn, request: Request,
                           session: AsyncSession = Depends(get_session),
                           owner: CurrentUser = Depends(require_role("owner"))):
    """Owner-only: rename, change plan, or adjust quota limits (finding k)."""
    try:
        t = await AuthService.update_workspace(
            session, tenant_id=owner.tenant_id, name=body.name, plan=body.plan, settings_patch=body.settings
        )
    except AuthError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await AuditService.log(tenant_id=owner.tenant_id, action="workspace.update", actor_id=owner.id,
                           actor_email=owner.email, ip=client_ip(request),
                           meta=body.model_dump(exclude_none=True))
    return _tenant_out(t)


@workspace_router.delete("", status_code=204)
async def delete_workspace(body: WorkspaceDeleteIn, request: Request,
                           session: AsyncSession = Depends(get_session),
                           owner: CurrentUser = Depends(require_role("owner"))):
    """Owner-only, name-confirmed, cascading workspace deletion (finding k). Irreversible."""
    from forge.models import Tenant

    t = await session.get(Tenant, owner.tenant_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not found")
    if body.confirm_name != t.name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "confirm_name does not match the workspace name")
    await AuditService.log(tenant_id=owner.tenant_id, action="workspace.delete", actor_id=owner.id,
                           actor_email=owner.email, ip=client_ip(request), meta={"name": t.name})
    await AuthService.delete_workspace(
        session, tenant_id=owner.tenant_id,
        checkpointer=getattr(request.app.state, "checkpointer", None),
    )


# --- API keys (finding h) ---
class ApiKeyCreateIn(BaseModel):
    name: str
    role: str = "editor"
    ttl_days: int | None = None


def _apikey_out(k, *, plaintext: str | None = None) -> dict:
    out = {"id": k.id, "name": k.name, "role": k.role, "status": k.status, "prefix": k.prefix,
           "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
           "expires_at": k.expires_at.isoformat() if k.expires_at else None}
    if plaintext is not None:
        out["key"] = plaintext  # shown ONCE at creation
    return out


@apikeys_router.get("")
async def list_api_keys(session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id),
                        _: CurrentUser = Depends(require_role("admin"))):
    from forge.services.apikeys import ApiKeyService

    return [_apikey_out(k) for k in await ApiKeyService.list(session, tenant_id)]


@apikeys_router.post("", status_code=201)
async def create_api_key(body: ApiKeyCreateIn, request: Request,
                         session: AsyncSession = Depends(get_session),
                         admin: CurrentUser = Depends(require_role("admin"))):
    from forge.services.apikeys import ApiKeyService

    # An admin must not be able to mint a key MORE privileged than themselves.
    from forge.services.auth import role_at_least

    if not role_at_least(admin.role, body.role):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot create a key more privileged than your role")
    try:
        key, plaintext = await ApiKeyService.create(
            session, tenant_id=admin.tenant_id, name=body.name, role=body.role,
            created_by=admin.id, ttl_days=body.ttl_days,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    await AuditService.log(tenant_id=admin.tenant_id, action="apikey.create", actor_id=admin.id,
                           actor_email=admin.email, resource_type="api_key", resource_id=key.id,
                           ip=client_ip(request), meta={"name": key.name, "role": key.role})
    return _apikey_out(key, plaintext=plaintext)


@apikeys_router.delete("/{key_id}", status_code=204)
async def revoke_api_key(key_id: str, request: Request,
                         session: AsyncSession = Depends(get_session),
                         admin: CurrentUser = Depends(require_role("admin"))):
    from forge.services.apikeys import ApiKeyService

    if not await ApiKeyService.revoke(session, tenant_id=admin.tenant_id, key_id=key_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    await AuditService.log(tenant_id=admin.tenant_id, action="apikey.revoke", actor_id=admin.id,
                           actor_email=admin.email, resource_type="api_key", resource_id=key_id,
                           ip=client_ip(request))
