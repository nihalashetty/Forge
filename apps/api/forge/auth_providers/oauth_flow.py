"""Shared authorization-code connect flow (PKCE + signed state).

Both the Auth Providers screen and the Connectors screen start the same 3-legged OAuth dance
against the same `/v1/oauth/callback`. Keeping the URL construction here means the security
properties - PKCE S256, a signed short-lived state, and carrying only the provider's declared
per-user dims - are implemented once instead of drifting between two call sites.
"""

from __future__ import annotations

import base64
import hashlib
import secrets as _secrets
from urllib.parse import urlencode

from forge.config import settings
from forge.models import AuthProvider
from forge.secrets.store import SecretStore
from forge.security import create_state_token


class OAuthNotConfigured(ValueError):
    """The provider is missing something the authorize step needs (client id, authorize URL)."""


def redirect_uri(cfg: dict) -> str:
    return cfg.get("redirect_uri") or f"{settings.public_base_url.rstrip('/')}/v1/oauth/callback"


def token_request(cfg: dict, data: dict) -> tuple[dict, dict]:
    """Form body + headers for a token-endpoint POST, given a body that already carries
    `client_id`/`client_secret`. Returns `(data, headers)`.

    Two things a naive POST gets wrong against real servers:

    * `Accept: application/json`. GitHub's token endpoint answers `application/x-www-form-
      urlencoded` without it, so a perfectly successful exchange then dies in `.json()`.
    * client_secret_basic. RFC 6749 §2.3.1 makes HTTP Basic the method a server MUST support
      and form-post the one it MAY; a few (Airtable) accept only Basic. `token_auth: "basic"`
      on the provider config moves the secret into the Authorization header. `client_id` stays
      in the body, which every server tolerates and some still require.
    """
    headers = {"Accept": "application/json"}
    if cfg.get("token_auth") != "basic":
        return {k: v for k, v in data.items() if v is not None}, headers
    client_id, client_secret = data.get("client_id") or "", data.get("client_secret") or ""
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    headers["Authorization"] = "Basic " + creds
    return {k: v for k, v in data.items() if v is not None and k != "client_secret"}, headers


async def build_authorize_url(
    ap: AuthProvider, *, tenant_id: str, project_id: str, context: dict | None = None,
    secrets: SecretStore | None = None,
) -> str:
    """The provider's authorize URL, carrying a signed state that binds the callback back to
    this tenant/project/provider (and, for a per-user provider, to the end user being connected).

    The PKCE verifier rides inside the SIGNED state. That is safe for a CONFIDENTIAL client -
    which is what Forge always registers, since it holds the client secret server-side in its
    own encrypted store - because the token exchange also requires that secret. A public client
    (no secret) would need the verifier held server-side instead; see routers/oauth.py.
    """
    cfg = ap.config or {}
    store = secrets or SecretStore()
    client_id = None
    if cfg.get("client_id_ref"):
        try:
            client_id = await store.read_ref(tenant_id=tenant_id, project_id=project_id, ref=cfg["client_id_ref"])
        except Exception as e:  # noqa: BLE001 - surface as a configuration problem, not a 500
            raise OAuthNotConfigured("client_id secret is not set") from e
    if not client_id:
        raise OAuthNotConfigured("client_id secret is not set")
    if not cfg.get("authorize_url"):
        raise OAuthNotConfigured("authorize_url is not configured")

    verifier = _secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    claims = {"tid": tenant_id, "pid": project_id, "ap": ap.id, "cv": verifier}
    per_user = cfg.get("per_user_context_keys") or []
    ctx = context or {}
    user_ctx = {k: ctx[k] for k in per_user if k in ctx}
    if user_ctx:
        claims["ctx"] = user_ctx

    q = {
        "response_type": "code",
        "client_id": str(client_id),
        "redirect_uri": redirect_uri(cfg),
        "state": create_state_token(claims),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if cfg.get("scope"):
        q["scope"] = cfg["scope"]
    # RFC 8707: bind the issued token to the resource it is for, so a token minted for one MCP
    # server can't be replayed against another. Only sent when the provider names a resource
    # (connectors set it to the MCP server URL); omitted otherwise to avoid upsetting servers
    # that reject unknown parameters.
    if cfg.get("resource"):
        q["resource"] = cfg["resource"]
    # Providers that only return a refresh_token when explicitly asked (Google, and anything
    # modeled on it). Harmless elsewhere - it is only sent when the manifest opts in.
    for key in ("access_type", "prompt"):
        if cfg.get(key):
            q[key] = cfg[key]
    # Anything else a specific vendor requires on the authorize URL. Applied last but never over
    # the protocol parameters above - a manifest must not be able to redirect the callback or
    # weaken PKCE by declaring `redirect_uri` or `code_challenge_method` as an "extra".
    for key, value in (cfg.get("authorize_params") or {}).items():
        if key not in q:
            q[key] = str(value)
    return f"{cfg['authorize_url']}?{urlencode(q)}"
