"""Centralized audit middleware.

Records every successful mutating request (POST/PUT/PATCH/DELETE) as an AuditLog row -
so create/update/delete of any resource is audited without each router opting in. Pure
ASGI (peeks at the response-start status only) so it never buffers a body and can't break
the SSE run/assistant streams. The actor is taken from the JWT when present, else the
seeded dev tenant; auth endpoints are skipped (already audited in their router).
"""

from __future__ import annotations

from forge.config import settings
from forge.security import TokenError, decode_token
from forge.services.audit import AuditService
from forge.util.clientip import resolve_client_ip

_SKIP_PREFIXES = ("/v1/auth", "/v1/audit")
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _actor_from_headers(headers: dict[bytes, bytes]) -> tuple[str | None, str | None]:
    auth = headers.get(b"authorization", b"").decode("latin-1")
    if auth[:7].lower() == "bearer ":
        try:
            claims = decode_token(auth[7:].strip(), expected_type="access")
            return claims.get("sub"), claims.get("tid")
        except TokenError:
            return None, None
    return None, None


def _client_ip(scope, headers: dict[bytes, bytes]) -> str | None:
    # Believe X-Forwarded-For only from a configured reverse proxy (settings.trusted_proxies) -
    # the SAME rule as deps.client_ip. Previously this trusted XFF unconditionally, so any
    # client could poison the audit IP.
    client = scope.get("client")
    peer = client[0] if client else None
    fwd = headers.get(b"x-forwarded-for")
    return resolve_client_ip(peer, fwd.decode("latin-1") if fwd else None, settings.trusted_proxies)


class AuditMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in _MUTATING:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await self.app(scope, receive, send)

        status_code = {"v": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code["v"] = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)

        if not (200 <= status_code["v"] < 400):
            return
        headers = dict(scope.get("headers") or [])
        actor_id, tenant_id = _actor_from_headers(headers)
        if tenant_id is None:
            app = scope.get("app")
            tenant_id = getattr(getattr(app, "state", None), "tenant_id", None)
        if not tenant_id:
            return
        await AuditService.log(
            tenant_id=tenant_id, action=f"{scope['method']} {path}", actor_id=actor_id,
            ip=_client_ip(scope, headers), status="ok", meta={"status_code": status_code["v"]},
        )
