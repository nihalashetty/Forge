"""Microsoft Teams channel - Bot Framework Activity handling.

Inbound: Teams POSTs an Activity to the bot messaging endpoint. Outbound: reply via the
Bot Connector REST API at the Activity's `serviceUrl`, authenticated with an AAD app
token (client-credentials). The Azure bot registration (app id + password) is supplied
by the customer and stored as channel secrets - Forge wires the protocol; you provide
the credentials.

Inbound is authenticated by verifying the Bot Framework JWT (`verify_bot_jwt`), so a random
POST to the public messaging endpoint can't drive the workflow or exfiltrate the bot token.
"""

from __future__ import annotations

import json
import logging
import time
from urllib.parse import urlparse

from forge.channels.retry import ChannelDeliveryError, retry_send
from forge.secrets.store import SecretStore
from forge.util.http import shared_async_client
from forge.util.ssrf import guarded_request

log = logging.getLogger("forge.channels.teams")

_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
_token_cache: dict[str, tuple[float, str]] = {}  # app_id -> (expires_at, token)

# Bot Framework token authority for INBOUND activity JWTs (channel -> bot). Distinct from the
# outbound token authority above (bot -> connector). These are fixed Microsoft endpoints.
_OPENID_CONFIG_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
_INBOUND_ISSUERS = ("https://api.botframework.com",)
_ADAPTIVE_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"
_jwks_cache: dict[str, object] = {"exp": 0.0, "keys": {}}  # {exp, keys: {kid: key}}


def parse_activity(activity: dict) -> dict:
    """Normalize an inbound Bot Framework Activity."""
    frm = activity.get("from") or {}
    conv = activity.get("conversation") or {}
    recip = activity.get("recipient") or {}
    return {
        "type": activity.get("type"),
        "text": (activity.get("text") or "").strip(),
        # Action.Submit / adaptive-card actions carry their data in `value` (usually no text).
        "value": activity.get("value"),
        "from_id": frm.get("id"),
        "from_name": frm.get("name"),
        "conversation_id": conv.get("id"),
        "service_url": activity.get("serviceUrl"),
        "recipient_id": recip.get("id"),
        "recipient_name": recip.get("name"),
        "activity_id": activity.get("id"),
        "locale": activity.get("locale"),
    }


def card_action_text(parsed: dict) -> str:
    """Turn a card action (Action.Submit `value`) into an input string so a button press drives
    the workflow the same way a typed message does (audit D). Prefers a human-readable field,
    falls back to a compact JSON dump of the submitted data."""
    v = parsed.get("value")
    if isinstance(v, dict):
        for k in ("text", "message", "action", "value", "id"):
            got = v.get(k)
            if isinstance(got, str) and got.strip():
                return got.strip()
        try:
            return json.dumps(v, default=str)
        except Exception:  # noqa: BLE001
            return str(v)
    if isinstance(v, str):
        return v.strip()
    return ""


def inbound_text(parsed: dict) -> str:
    """The effective user text for an inbound activity: the typed message, else a card action."""
    return parsed.get("text") or card_action_text(parsed)


def build_reply_activity(incoming: dict, text: str) -> dict:
    """A `message` Activity replying to `incoming` (parsed)."""
    return {
        "type": "message",
        "from": {"id": incoming.get("recipient_id"), "name": incoming.get("recipient_name")},
        "recipient": {"id": incoming.get("from_id"), "name": incoming.get("from_name")},
        "conversation": {"id": incoming.get("conversation_id")},
        "replyToId": incoming.get("activity_id"),
        "text": text,
    }


def build_card_activity(incoming: dict, card: dict, *, text: str | None = None) -> dict:
    """A `message` Activity carrying an Adaptive Card attachment (audit D). `card` is the raw
    Adaptive Card content (a dict with `type: AdaptiveCard`); `text` is optional fallback text."""
    activity = build_reply_activity(incoming, text or "")
    activity["attachments"] = [{"contentType": _ADAPTIVE_CARD_CONTENT_TYPE, "content": card}]
    return activity


# --- inbound JWT verification (channel -> bot) --------------------------------------------


async def _bot_signing_keys() -> dict:
    """Fetch + cache the Bot Framework JWT signing keys (kid -> public key) from the connector's
    OpenID metadata. Cached ~24h (keys rotate slowly). Best-effort; returns {} on fetch error."""
    now = time.time()
    if _jwks_cache["keys"] and now < float(_jwks_cache["exp"]):
        return _jwks_cache["keys"]  # type: ignore[return-value]
    try:
        import jwt as _jwt  # PyJWT

        client = shared_async_client()
        meta = (await client.get(_OPENID_CONFIG_URL, timeout=15)).json()
        jwks = (await client.get(meta["jwks_uri"], timeout=15)).json()
        keys: dict[str, object] = {}
        for k in jwks.get("keys", []):
            kid = k.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = _jwt.PyJWK.from_dict(k).key
            except Exception:  # noqa: BLE001 - skip an unparseable key
                continue
        _jwks_cache["keys"] = keys
        _jwks_cache["exp"] = now + 24 * 3600
        return keys
    except Exception:  # noqa: BLE001 - metadata/JWKS fetch failed
        log.warning("Teams JWT: failed to fetch Bot Framework signing keys", exc_info=True)
        return {}


async def verify_bot_jwt(auth_header: str | None, *, app_id: str) -> bool:
    """Verify an inbound Bot Framework `Authorization: Bearer <jwt>` (audit D).

    Validates the RS256 signature against the connector JWKS, the issuer, the audience (== the
    bot's app id), and expiry. Returns True only on a fully valid token.

    PARTIAL: this covers the standard channel->bot token path (signature/issuer/audience/exp).
    It does NOT additionally validate the `serviceurl` claim against the activity's serviceUrl,
    nor the government-cloud issuers - the serviceUrl allow-list in send_reply is the compensating
    control for the token-exfiltration risk."""
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return False
    token = auth_header.split(" ", 1)[1].strip()
    try:
        import jwt as _jwt

        # Parse the header FIRST (fast, no network) so a malformed token fails without a JWKS
        # fetch; only reach out for signing keys once the token is at least well-formed.
        header = _jwt.get_unverified_header(token)
        keys = await _bot_signing_keys()
        key = keys.get(header.get("kid")) if keys else None
        if key is None:
            return False
        _jwt.decode(
            token, key=key, algorithms=["RS256"], audience=app_id,
            issuer=list(_INBOUND_ISSUERS), options={"require": ["exp", "aud", "iss"]},
        )
        return True
    except Exception:  # noqa: BLE001 - any validation failure => not authenticated
        log.warning("Teams JWT: inbound token verification failed", exc_info=True)
        return False


async def _bot_token(app_id: str, app_password: str) -> str:
    cached = _token_cache.get(app_id)
    now = time.time()
    if cached and now < cached[0] - 60:
        return cached[1]
    data = {
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": app_password,
        "scope": "https://api.botframework.com/.default",
    }
    r = await shared_async_client().post(_TOKEN_URL, data=data, timeout=30)
    r.raise_for_status()
    body = r.json()
    token = body["access_token"]
    _token_cache[app_id] = (now + int(body.get("expires_in", 3600)), token)
    return token


# The Bot Connector `serviceUrl` arrives on the INBOUND activity. Even once the JWT is verified,
# treat serviceUrl as attacker-influenced and only ever send the bot token to a genuine Microsoft
# Bot Framework endpoint - never an arbitrary or internal host (audit H3). These are the
# commercial-cloud host patterns; sovereign clouds (GCC-High/DoD) would extend this list.
_TRUSTED_SERVICE_URL_HOSTS = ("smba.trafficmanager.net",)
_TRUSTED_SERVICE_URL_SUFFIXES = (".botframework.com", ".smba.trafficmanager.net")


def _is_trusted_service_url(url: str) -> bool:
    try:
        p = urlparse(url or "")
    except Exception:  # noqa: BLE001
        return False
    host = (p.hostname or "").lower().rstrip(".")
    return p.scheme == "https" and bool(host) and (
        host in _TRUSTED_SERVICE_URL_HOSTS or host.endswith(_TRUSTED_SERVICE_URL_SUFFIXES)
    )


async def _post_activity(channel, incoming: dict, activity: dict) -> bool:
    """POST an Activity (message or card) to Teams via the Connector API, with bounded retry +
    429/Retry-After-aware backoff. Returns True on success, False when not configured / the
    serviceUrl is untrusted (a no-op). Raises after exhausting retries (audit D/E)."""
    cfg = channel.config or {}
    app_id = cfg.get("app_id")
    service_url = incoming.get("service_url")
    conv_id = incoming.get("conversation_id")
    if not (app_id and service_url and conv_id and cfg.get("app_password_ref")):
        return False
    if not _is_trusted_service_url(service_url):
        log.warning("Teams send: refusing reply to untrusted serviceUrl %r", service_url)
        return False
    try:
        app_password = await SecretStore().read_ref(
            tenant_id=channel.tenant_id, project_id=channel.project_id, ref=cfg["app_password_ref"]
        )
    except Exception:  # noqa: BLE001
        return False
    url = f"{service_url.rstrip('/')}/v3/conversations/{conv_id}/activities"

    async def _attempt(_n: int) -> bool:
        token = await _bot_token(app_id, str(app_password))
        # Route through the SSRF guard too (defense-in-depth alongside the serviceUrl allow-list).
        r = await guarded_request(
            shared_async_client(), "POST", url,
            headers={"Authorization": f"Bearer {token}"}, json=activity, timeout=30,
        )
        if r.status_code == 429:
            retry_after = _parse_retry_after(r.headers.get("Retry-After"))
            raise ChannelDeliveryError("Teams 429 (rate limited)", retry_after=retry_after)
        if r.status_code >= 500:
            raise ChannelDeliveryError(f"Teams {r.status_code} (server error)")
        r.raise_for_status()
        return True

    return await retry_send(_attempt, label=f"teams->{conv_id}")


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds only; ignore HTTP-date form)."""
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except (TypeError, ValueError):
        return None


async def send_reply(channel, incoming: dict, text: str) -> bool:
    """POST a text reply Activity back to Teams. See `_post_activity` for the return contract."""
    return await _post_activity(channel, incoming, build_reply_activity(incoming, text))


async def send_card(channel, incoming: dict, card: dict, *, text: str | None = None) -> bool:
    """POST an Adaptive Card reply Activity back to Teams (audit D)."""
    return await _post_activity(channel, incoming, build_card_activity(incoming, card, text=text))
