"""Public inbound webhook endpoint for `webhook_in` triggers.

Authenticated by the unguessable per-trigger key in the path (+ optional HMAC
signature). No JWT - external systems POST here. Rate-limited per trigger.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status

from forge.db.base import SessionLocal
from forge.deps import get_run_service
from forge.secrets.store import SecretStore
from forge.services.dispatch import dispatch_trigger
from forge.services.runs import RunService
from forge.services.triggers import TriggerService
from forge.util.ratelimit import idempotency, rate_limiter
from forge.util.tasks import spawn

log = logging.getLogger("forge.hooks")

router = APIRouter(prefix="/v1/hooks", tags=["hooks"])

# Delivery-id headers common providers send for at-least-once retries; used to dedupe so a
# provider re-delivery doesn't double-run the workflow (duplicate side effects). A trigger may
# name its own via config.dedupe_header, or opt into body-hash dedupe via config.dedupe_body.
_DELIVERY_HEADERS = ("X-GitHub-Delivery", "X-Request-Id", "Idempotency-Key", "X-Delivery-Id", "X-Event-Id")

# Default replay window (seconds) for signature schemes that sign a timestamp (Stripe/Slack).
# A signature older/newer than this is rejected so a captured request can't be replayed later.
# Per-trigger override: config.signature_tolerance_seconds.
_SIG_TOLERANCE_DEFAULT = 300


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


def _hmac_hex(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _within_tolerance(ts_str: str | None, tolerance: int) -> bool:
    """True if a signed unix timestamp is within `tolerance` seconds of now (replay guard).
    `tolerance <= 0` disables the check (accept any timestamp)."""
    if tolerance <= 0:
        return True
    try:
        ts = float(str(ts_str).strip())
    except (TypeError, ValueError):
        return False
    return abs(time.time() - ts) <= tolerance


def _parse_kv_header(value: str) -> dict[str, str]:
    """Parse a `k=v,k=v` header (Stripe-Signature) -> {k: last-v}. Multiple values for one key
    (e.g. several v1=) are handled by the caller via the raw header; this keeps the last."""
    out: dict[str, str] = {}
    for part in (value or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _verify_stripe(secret: str, request: Request, body: bytes, tolerance: int) -> bool:
    # Stripe-Signature: t=<unix>,v1=<hexmac>[,v1=<hexmac>]. Signed payload = "<t>.<body>".
    header = request.headers.get("Stripe-Signature") or request.headers.get("stripe-signature")
    if not header:
        return False
    fields = _parse_kv_header(header)
    t = fields.get("t")
    if not _within_tolerance(t, tolerance):
        return False
    expected = _hmac_hex(secret, f"{t}.".encode() + body)
    provided = [p.split("=", 1)[1].strip() for p in header.split(",") if p.strip().startswith("v1=")]
    return any(hmac.compare_digest(expected, p) for p in provided)


def _verify_slack(secret: str, request: Request, body: bytes, tolerance: int) -> bool:
    # X-Slack-Signature: v0=<hexmac>; base string = "v0:<ts>:<body>"; ts from X-Slack-Request-Timestamp.
    ts = request.headers.get("X-Slack-Request-Timestamp") or request.headers.get("x-slack-request-timestamp")
    provided = request.headers.get("X-Slack-Signature") or request.headers.get("x-slack-signature")
    if not ts or not provided or not _within_tolerance(ts, tolerance):
        return False
    expected = "v0=" + _hmac_hex(secret, b"v0:" + ts.encode() + b":" + body)
    return hmac.compare_digest(expected, provided.strip())


def _verify_hmac_sha256(secret: str, request: Request, body: bytes) -> bool:
    # Default / GitHub-style: HMAC-SHA256 over the raw body; header tolerates a "sha256=" prefix.
    signature = request.headers.get("x-forge-signature") or request.headers.get("X-Hub-Signature-256")
    if not signature:
        return False
    expected = _hmac_hex(secret, body)
    provided = signature.split("=", 1)[-1].strip()  # tolerate "sha256=<hex>"
    return hmac.compare_digest(expected, provided)


async def _verify_signature(trigger, request: Request, body: bytes) -> bool:
    """Verify the inbound signature under the trigger's configured scheme (audit I).

    Schemes (config.signature_scheme): "hmac_sha256" (default; GitHub-style, raw-body HMAC),
    "stripe" (t=,v1= over "<t>.<body>"), "slack" (v0: over "v0:<ts>:<body>"). Stripe/Slack also
    enforce a signed-timestamp tolerance window (config.signature_tolerance_seconds, default 300)
    to block replays."""
    cfg = trigger.config or {}
    if not cfg.get("require_signature"):
        return True
    if not cfg.get("secret_ref"):
        return False
    try:
        secret = str(await SecretStore().read_ref(
            tenant_id=trigger.tenant_id, project_id=trigger.project_id, ref=cfg["secret_ref"]
        ))
    except Exception:  # noqa: BLE001
        return False
    scheme = (cfg.get("signature_scheme") or "hmac_sha256").lower()
    tolerance = int(cfg.get("signature_tolerance_seconds", _SIG_TOLERANCE_DEFAULT))
    try:
        if scheme == "stripe":
            return _verify_stripe(secret, request, body, tolerance)
        if scheme == "slack":
            return _verify_slack(secret, request, body, tolerance)
        if scheme in ("hmac_sha256", "hmac", "github", "default"):
            return _verify_hmac_sha256(secret, request, body)
    except Exception:  # noqa: BLE001 - a malformed header must fail closed, not 500
        log.warning("signature verification error for trigger %s (scheme=%s)", trigger.id, scheme, exc_info=True)
        return False
    log.warning("unknown signature_scheme %r for trigger %s", scheme, trigger.id)
    return False


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
    if not await _verify_signature(trigger, request, raw):
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
