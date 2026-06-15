"""SSRF egress-guard tests (offline: uses IP literals + pre-resolution checks)."""

from __future__ import annotations

import pytest

from forge.util.ssrf import EgressBlocked, EgressPolicy, validate_url

_BLOCK = EgressPolicy(block_private=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost/x",  # resolves to loopback
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://[::1]/x",
        "http://0.0.0.0/x",
        "http://[::ffff:127.0.0.1]/x",  # IPv4-mapped loopback
    ],
)
async def test_blocks_internal_targets(url):
    with pytest.raises(EgressBlocked):
        await validate_url(url, _BLOCK)


@pytest.mark.parametrize("url", ["ftp://example.com", "file:///etc/passwd", "gopher://x"])
async def test_blocks_non_http_schemes(url):
    with pytest.raises(EgressBlocked):
        await validate_url(url, _BLOCK)


async def test_allows_public_ip_literal():
    assert await validate_url("https://8.8.8.8/x", _BLOCK) == "https://8.8.8.8/x"


async def test_deny_list_blocks_before_resolution():
    pol = EgressPolicy(block_private=True, deny_hosts=("evil.example",))
    with pytest.raises(EgressBlocked):
        await validate_url("https://api.evil.example/x", pol)  # parent-domain match


async def test_allow_list_blocks_other_hosts():
    pol = EgressPolicy(block_private=True, allow_hosts=("good.example",))
    with pytest.raises(EgressBlocked):
        await validate_url("https://other.example/x", pol)
    # host inside the allowed parent domain + public literal passes
    assert await validate_url("https://8.8.8.8/x", EgressPolicy(allow_hosts=("8.8.8.8",)))


async def test_block_private_disabled_allows_loopback():
    assert await validate_url("http://127.0.0.1/x", EgressPolicy(block_private=False))
