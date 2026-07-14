"""Public inbound webhook endpoint for `webhook_in` triggers.

Authenticated by the unguessable per-trigger key in the path (+ optional HMAC
signature). No JWT - external systems POST here. Rate-limited per trigger.
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, status

from forge.db.base import SessionLocal
from forge.deps import get_run_service
from forge.secrets.store import SecretStore
from forge.services.dispatch import dispatch_trigger
from forge.services.runs import RunService
from forge.services.triggers import TriggerService
from forge.util.ratelimit import idempotency, rate_limiter
from forge.util.tasks import spawn

router = APIRouter(prefix="/v1/hooks", tags=["hooks"])

# Delivery-id headers common providers send for at-least-once retries; used to dedupe so a
# provider re-delivery doesn't double-run the workflow (duplicate side effects). A trigger may
# name its own via config.dedupe_header, or opt into body-hash dedupe via config.dedupe_body.
_DELIVERY_HEADERS = ("X-GitHub-Delivery", "X-Request-Id", "Idempotency-Key", "X-Delivery-Id", "X-Event-Id")


def _dedupe_key(trigger, request: Request, raw: bytes) -> str | None:
    cfg = trigger.config or {}
    names = [cfg["dedupe_header"]] if cfg.get("dedupe_header") else list(_DELIVERY_HEADERS)
    for name in names:
        if not name:
            continue
        v = request.headers.get(name) or request.headers.get(name.lower())
        if v:
            return v.strip()
    if cfg.get("dedupe_body"):
        return hashlib.sha256(raw).hexdigest()
    return None


async def _verify_signature(trigger, body: bytes, signature: str | None) -> bool:
    cfg = trigger.config or {}
    if not cfg.get("require_signature"):
        return True
    if not signature or not cfg.get("secret_ref"):
        return False
    try:
        secret = await SecretStore().read_ref(
            tenant_id=trigger.tenant_id, project_id=trigger.project_id, ref=cfg["secret_ref"]
        )
    except Exception:  # noqa: BLE001
        return False
    mac = hmac.new(str(secret).encode(), body, hashlib.sha256).hexdigest()
    provided = signature.split("=", 1)[-1].strip()  # tolerate "sha256=<hex>"
    return hmac.compare_digest(mac, provided)


@router.post("/{key}")
async def inbound_webhook(
    key: str,
    request: Request,
    wait: bool = False,
    run_service: RunService = Depends(get_run_service),
):
    async with SessionLocal() as s:
        trigger = await TriggerService.by_key(s, key)
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown or disabled webhook")

    if not rate_limiter.allow(f"hook:{key}", rate=120, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "webhook rate limit exceeded")

    raw = await request.body()
    sig = request.headers.get("x-forge-signature") or request.headers.get("X-Hub-Signature-256")
    if not await _verify_signature(trigger, raw, sig):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")

    # Idempotency: dedupe an at-least-once redelivery (same delivery id / configured key) so the
    # workflow doesn't run twice. Claim the key BEFORE dispatch so concurrent duplicates collapse.
    dedupe = _dedupe_key(trigger, request, raw)
    if dedupe:
        ik = f"hook:{trigger.id}:{dedupe}"
        if idempotency.get(ik) is not None:
            return {"accepted": True, "trigger": trigger.id, "deduplicated": True}
        idempotency.put(ik, {"accepted": True})

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - non-JSON body
        payload = raw.decode("utf-8", "replace")

    if wait:
        result = await dispatch_trigger(run_service, trigger, payload)
        return result
    # fire-and-forget: ack immediately, run in a TRACKED background task so failures are
    # logged (not silently swallowed) and a flood can't spawn unbounded coroutines (F4).
    accepted = spawn(dispatch_trigger(run_service, trigger, payload), name=f"webhook:{trigger.id}")
    if not accepted:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "server busy; retry shortly")
    return {"accepted": True, "trigger": trigger.id}
