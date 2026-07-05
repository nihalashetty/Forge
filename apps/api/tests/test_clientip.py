"""Client-IP trust rule (anti-spoofing): X-Forwarded-For is believed ONLY from a configured
reverse proxy, so a directly-connected client cannot forge its IP for per-IP rate limits or
audit logs. Covers both call sites (deps.client_ip and the audit middleware) via the shared
helper they now both use."""

from forge.util.clientip import resolve_client_ip


def test_no_trusted_proxies_ignores_xff():
    # Default (direct exposure): a client-supplied X-Forwarded-For must be ignored.
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", []) == "203.0.113.9"


def test_trusted_proxy_uses_leftmost_xff():
    # Behind a configured proxy, the original client is the left-most XFF entry.
    assert resolve_client_ip("10.0.0.5", "1.2.3.4, 10.0.0.5", ["10.0.0.5"]) == "1.2.3.4"


def test_untrusted_peer_ignores_xff():
    # A peer that is NOT a configured proxy cannot get its XFF believed.
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", ["10.0.0.5"]) == "203.0.113.9"


def test_wildcard_trusts_any_peer():
    assert resolve_client_ip("203.0.113.9", "1.2.3.4", ["*"]) == "1.2.3.4"


def test_no_xff_returns_peer():
    assert resolve_client_ip("203.0.113.9", None, ["*"]) == "203.0.113.9"


def test_no_client_and_no_xff_is_none():
    assert resolve_client_ip(None, None, []) is None
