"""Load the shared JSON Schemas (packages/schemas) into a referencing Registry.

The doc's `$id`/`$ref` use relative-looking ids ("forge/common"). Standard URI
resolution would mangle nested refs (e.g. from base "forge/nodes/agent", the ref
"forge/common" resolves to "forge/nodes/forge/common"). We avoid that by rewriting
every `$id` and every "forge/..."-prefixed `$ref` to an absolute base URL on load,
so all cross-file refs become absolute exact lookups.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from forge.config import settings

BASE = "https://forge.dev/schemas/"


def _absolutize(node: Any) -> Any:
    """Rewrite $id and forge/* $ref values to absolute URIs (recursive)."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "$id" and isinstance(v, str) and not v.startswith("http"):
                out[k] = BASE + v
            elif k == "$ref" and isinstance(v, str) and v.startswith("forge/"):
                out[k] = BASE + v
            else:
                out[k] = _absolutize(v)
        return out
    if isinstance(node, list):
        return [_absolutize(x) for x in node]
    return node


@functools.lru_cache(maxsize=1)
def _load() -> tuple[Registry, dict[str, dict], dict[str, dict]]:
    """Return (registry, raw_schemas_by_id, middleware_config_schemas)."""
    root = Path(settings.schemas_dir) / "forge"
    if not root.exists():
        raise FileNotFoundError(f"Schemas dir not found: {root}")

    raw_by_id: dict[str, dict] = {}
    resources: list[tuple[str, Resource]] = []
    for path in sorted(root.rglob("*.json")):
        data = _absolutize(json.loads(path.read_text(encoding="utf-8")))
        sid = data.get("$id")
        if not sid:
            continue
        raw_by_id[sid] = data
        resources.append((sid, Resource.from_contents(data, default_specification=DRAFT202012)))

    registry = Registry().with_resources(resources)

    # Middleware per-type config schemas live under a non-standard `config_schemas` key.
    mw = raw_by_id.get(BASE + "forge/middleware", {})
    mw_config_schemas = mw.get("config_schemas", {})

    return registry, raw_by_id, mw_config_schemas


def get_registry() -> Registry:
    return _load()[0]


def node_schema_ref(node_type: str) -> dict:
    """A schema that simply $refs the node type's schema (resolved via registry).

    Uses the node's registered `schema_id` (so types whose schema file isn't named after
    the type - e.g. webhook_in -> forge/nodes/trigger_webhook - still resolve)."""
    from forge.engine.registry import NODE_REGISTRY

    spec = NODE_REGISTRY.get(node_type)
    schema_id = spec.schema_id if spec else f"forge/nodes/{node_type}"
    return {"$ref": f"{BASE}{schema_id}"}


def middleware_config_schema(mw_type: str) -> dict | None:
    return _load()[2].get(mw_type)


def middleware_types() -> list[str]:
    return sorted(_load()[2].keys())


def raw_schema(schema_id: str) -> dict | None:
    """The raw (absolutized) schema document for an id like 'forge/nodes/router'."""
    return _load()[1].get(BASE + schema_id)


def _pointer(path) -> str:
    return "/" + "/".join(str(p) for p in path) if path else "/"


def validate(instance: Any, schema: dict) -> list[dict]:
    """Validate `instance` against `schema`; return [] or a list of field errors."""
    validator = Draft202012Validator(schema, registry=get_registry())
    errors = []
    for err in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path)):
        errors.append({"pointer": _pointer(err.absolute_path), "message": err.message})
    return errors


def validate_against_id(instance: Any, schema_id: str) -> list[dict]:
    """Validate against a registered schema id, e.g. 'forge/workflow'."""
    return validate(instance, {"$ref": BASE + schema_id})


def reset_cache() -> None:
    _load.cache_clear()
