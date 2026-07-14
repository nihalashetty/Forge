"""Microsoft Teams channel - Bot Framework Activity handling.

Inbound: Teams POSTs an Activity to the bot messaging endpoint. Outbound: reply via the
Bot Connector REST API at the Activity's `serviceUrl`, authenticated with an AAD app
token (client-credentials). The Azure bot registration (app id + password) is supplied
by the customer and stored as channel secrets - Forge wires the protocol; you provide
the credentials.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from forge.secrets.store import SecretStore
from forge.util.http import shared_async_client
from forge.util.ssrf import guarded_request

log = logging.getLogger("forge.channels.teams")

_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
_token_cache: dict[str, tuple[float, str]] = {}  # app_id -> (expires_at, token)


def parse_activity(activity: dict) -> dict:
    """Normalize an inbound Bot Framework Activity."""
    frm = activity.get("from") or {}
    conv = activity.get("conversation") or {}
    recip = activity.get("recipient") or {}
    return {
        "type": activity.get("type"),
        "text": (activity.get("text") or "").strip(),
        "from_id": frm.get("id"),
        "from_name": frm.get("name"),
        "conversation_id": conv.get("id"),
        "service_url": activity.get("serviceUrl"),
        "recipient_id": recip.get("id"),
        "recipient_name": recip.get("name"),
        "activity_id": activity.get("id"),
        "locale": activity.get("locale"),
    }


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


# The Bot Connector `serviceUrl` arrives on the INBOUND activity, which is NOT signature-verified,
# so it is attacker-controllable. send_reply mints and sends a valid AAD bot token to it, so it MUST
# be a genuine Microsoft Bot Framework endpoint - otherwise a forged activity could exfiltrate the
# token or point us at an internal host (audit H3). These are the commercial-cloud host patterns;
# sovereign clouds (GCC-High/DoD) would extend this list.
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


async def send_reply(channel, incoming: dict, text: str) -> None:
    """POST the reply Activity back to Teams via the Connector API. No-op if not configured."""
    cfg = channel.config or {}
    app_id = cfg.get("app_id")
    service_url = incoming.get("service_url")
    conv_id = incoming.get("conversation_id")
    if not (app_id and service_url and conv_id and cfg.get("app_password_ref")):
        return
    # serviceUrl is attacker-controllable (unsigned inbound activity); only ever send the bot token
    # to a genuine Bot Framework endpoint, never an arbitrary or internal host (audit H3).
    if not _is_trusted_service_url(service_url):
        log.warning("Teams send_reply: refusing reply to untrusted serviceUrl %r", service_url)
        return
    try:
        app_password = await SecretStore().read_ref(
            tenant_id=channel.tenant_id, project_id=channel.project_id, ref=cfg["app_password_ref"]
        )
    except Exception:  # noqa: BLE001
        return
    token = await _bot_token(app_id, str(app_password))
    url = f"{service_url.rstrip('/')}/v3/conversations/{conv_id}/activities"
    # Route through the SSRF guard too (defense-in-depth alongside the serviceUrl allow-list above).
    await guarded_request(
        shared_async_client(), "POST", url,
        headers={"Authorization": f"Bearer {token}"},
        json=build_reply_activity(incoming, text), timeout=30,
    )
