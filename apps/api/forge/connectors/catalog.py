"""The bundled connector catalog - JSON manifests shipped inside the package.

Loading is lazy, cached, and performs NO network I/O: the catalog is a directory read. That is
the whole independence guarantee in one function - Forge boots, lists connectors, and installs
them on a machine with no outbound internet access at all (the connector's own API is the only
thing it ever talks to, and only when a tool actually runs).

A malformed manifest is skipped with a logged warning rather than taking down the catalog: one
bad file in a 25-file directory must not blank the Connectors screen.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from forge.connectors.manifest import ConnectorManifest, ManifestError, parse_manifest

log = logging.getLogger("forge.connectors")

CATALOG_DIR = Path(__file__).parent / "catalog"
#: Manifests that are shipped but NOT in the gallery, because they can't honour its promise:
#: one click, no keys, your own account. An API key, a bot token or a per-tenant subdomain has
#: to be typed by somebody, so these are offered as starting points for the custom-connector
#: path instead of as a form bolted onto a screen that says there are no forms.
EXAMPLES_DIR = Path(__file__).parent / "examples"


def _read_dir(directory: Path) -> dict[str, tuple[ConnectorManifest, dict]]:
    """Parsed manifests paired with the JSON exactly as authored.

    The raw document is kept because the custom-connector form offers examples as a STARTING
    POINT to edit: round-tripping a parsed model there would either bury the author's file under
    every defaulted field or, with defaults excluded, silently drop the backend discriminator and
    produce a manifest that no longer validates.
    """
    out: dict[str, tuple[ConnectorManifest, dict]] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            manifest = parse_manifest(data)
        except (OSError, json.JSONDecodeError, ManifestError) as e:
            log.warning("skipping connector manifest %s: %s", path.name, e)
            continue
        if manifest.slug in out:
            log.warning("duplicate connector slug %r in %s - keeping the first", manifest.slug, path.name)
            continue
        out[manifest.slug] = (manifest, data)
    return out


@lru_cache(maxsize=1)
def _load() -> dict[str, ConnectorManifest]:
    return {slug: m for slug, (m, _) in _read_dir(CATALOG_DIR).items()}


@lru_cache(maxsize=1)
def _load_examples() -> dict[str, tuple[ConnectorManifest, dict]]:
    return _read_dir(EXAMPLES_DIR)


def list_examples() -> list[tuple[ConnectorManifest, dict]]:
    """Each example as `(manifest, source_json)`, name-sorted."""
    return sorted(_load_examples().values(), key=lambda pair: pair[0].name.lower())


class _Catalog:
    """Dict-like view over the bundled manifests (so callers can do `CATALOG["slack"]`)."""

    def __getitem__(self, slug: str) -> ConnectorManifest:
        return _load()[slug]

    def __contains__(self, slug: object) -> bool:
        return slug in _load()

    def __iter__(self):
        return iter(_load().values())

    def __len__(self) -> int:
        return len(_load())

    def get(self, slug: str) -> ConnectorManifest | None:
        return _load().get(slug)


CATALOG = _Catalog()


def list_manifests() -> list[ConnectorManifest]:
    """Every bundled manifest, name-sorted (the order the gallery renders in)."""
    return sorted(_load().values(), key=lambda m: m.name.lower())


def get_manifest(slug: str) -> ConnectorManifest | None:
    return _load().get(slug)


def categories() -> list[str]:
    seen: list[str] = []
    for m in list_manifests():
        for c in m.categories:
            if c not in seen:
                seen.append(c)
    return sorted(seen)


def roles() -> list[str]:
    """Every role any manifest claims to be popular for - drives the "Popular for <role>"
    picker. Sorted, with the most-referenced roles first so the default lands on a useful one."""
    counts: dict[str, int] = {}
    for m in list_manifests():
        for r in m.roles:
            counts[r] = counts.get(r, 0) + 1
    return [r for r, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


def reload_catalog() -> None:
    """Drop the cache (tests, and a hot-reload during catalog authoring)."""
    _load.cache_clear()
    _load_examples.cache_clear()
