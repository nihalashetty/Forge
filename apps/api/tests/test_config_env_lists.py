"""List-of-strings settings parse leniently from env — JSON array, comma-separated, or blank —
so a stray bracket/space (e.g. from a `${VAR:-[]}` compose interpolation) can't crash startup.

Regression: the api container failed to boot with `FORGE_EGRESS_ALLOW_PRIVATE_HOSTS='['`
(pydantic-settings tried json.loads('[') -> JSONDecodeError -> SettingsError).
"""

from forge.config import Settings, _as_str_list


def test_blank_and_mangled_are_empty():
    for v in ("", "   ", "[", "[]", None):
        assert _as_str_list(v) == []


def test_json_array():
    assert _as_str_list('["localhost","127.0.0.1"]') == ["localhost", "127.0.0.1"]


def test_comma_separated():
    assert _as_str_list("localhost, 127.0.0.1 , host.docker.internal") == [
        "localhost", "127.0.0.1", "host.docker.internal",
    ]


def test_unquoted_or_mangled_bracketed():
    assert _as_str_list("[localhost,127.0.0.1]") == ["localhost", "127.0.0.1"]


def test_passthrough_existing_list():
    assert _as_str_list(["a", "b"]) == ["a", "b"]


def test_settings_boots_with_the_crashing_value(monkeypatch):
    monkeypatch.setenv("FORGE_EGRESS_ALLOW_PRIVATE_HOSTS", "[")  # exact value that crashed the container
    s = Settings()
    assert s.egress_allow_private_hosts == []


def test_settings_parses_real_egress_list(monkeypatch):
    monkeypatch.setenv("FORGE_EGRESS_ALLOW_PRIVATE_HOSTS", "localhost,127.0.0.1")
    s = Settings()
    assert s.egress_allow_private_hosts == ["localhost", "127.0.0.1"]
