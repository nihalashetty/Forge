"""Import / Export of the four authorable entity types as portable JSON bundles.

The Tools, Workflows, Components, and Agents screens each export a selection of their
rows to a single-type JSON bundle (a downloadable file) and import such a bundle back
into ANY project. Export serializes every authored field of each row (runtime-only junk
like a tool's `_last_test` is stripped, and secret VALUES are never included - only the
existing `secret://…` refs). Import re-creates each item exactly like a fresh create +
save: a new UUID, the same create-path validation, and a version-history snapshot - so an
imported entity is indistinguishable from one built in the console.

Two things make import faithful without ever clobbering existing data:

* **Auto-rename, never overwrite** - a name that already exists in the target project (or
  was just used earlier in the same bundle) gets a `_imported` / `_imported_N` suffix. This
  is required for components (unique-name DB constraint) and applied uniformly.
* **Intra-bundle id remap** - entities reference each other by DB id (a workflow's
  `subworkflow.workflow_id`, an agent's `config.tools`/`components`, …). Because every
  created row gets a fresh id, a second pass rewrites any reference to an id that WAS in the
  bundle to its new id. References to rows NOT in the bundle (e.g. a tool an agent uses that
  lives only in the source project) are left as-is - the compiler already tolerates unknown
  ids - and a tool's `auth_provider_id` is kept only if that provider exists in the target
  project, else cleared (with a warning) so it can never dangle onto a foreign project's row.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models import Agent, AuthProvider, Component, Project, Tool, Workflow
from forge.schemas.contracts import validate_against_id
from forge.services.agents import AgentService
from forge.services.components import ComponentService
from forge.services.tools import ToolService
from forge.services.versions import safe_snapshot
from forge.services.workflows import WorkflowService
from forge.util.serialize import jsonable

BUNDLE_FORMAT = "forge.bundle/1"
EXPORTABLE = ("tool", "workflow", "component", "agent")

# entity_type -> ORM model, so a single scoped query fetches the selected rows.
_MODEL: dict[str, type] = {
    "tool": Tool,
    "workflow": Workflow,
    "component": Component,
    "agent": Agent,
}

# Provider-safe identifier charset a component name must satisfy (it is used verbatim as the
# LLM tool name). Mirrors ComponentCreate's pattern in routers/components.py.
_COMPONENT_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")
_COMPONENT_NAME_MAX = 64


# --------------------------------------------------------------------------- export --------


def _export_tool(t: Tool) -> dict:
    cfg = dict(t.config or {})
    cfg.pop("_last_test", None)  # last-test payload is runtime state, not authored config
    return {
        "id": t.id,
        "name": t.name,
        "kind": t.kind,
        "config": cfg,
        "auth_provider_id": t.auth_provider_id,
        "enabled": t.enabled,
    }


def _export_workflow(w: Workflow) -> dict:
    return {
        "id": w.id,
        "name": w.name,
        "description": w.description,
        "canvas": w.canvas or {},
        "executable": w.executable or {},
        # Carried for reference; import always lands a workflow as a draft (publish is a
        # separate, governance-checked action), so this is informational only.
        "status": w.status,
    }


def _export_component(c: Component) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "title": c.title,
        "description": c.description,
        "props_schema": c.props_schema or {},
        "html": c.html or "",
        "css": c.css or "",
        "actions": c.actions or [],
        "sample_props": c.sample_props or {},
        "kind": c.kind,
        "enabled": c.enabled,
    }


def _export_agent(a: Agent) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "config": a.config or {},
        # Original author, for display only; the importer becomes the new created_by.
        "created_by_email": a.created_by_email,
    }


_EXPORTERS = {
    "tool": _export_tool,
    "workflow": _export_workflow,
    "component": _export_component,
    "agent": _export_agent,
}


# --------------------------------------------------------------------------- helpers -------


def _unique_name(base: str, used: set[str], *, max_len: int | None = None) -> str:
    """A name not already in `used`, appending `_imported` / `_imported_N` on collision.
    `max_len` (components) trims the base so the suffixed result still fits the column /
    identifier limit."""
    base = (base or "").strip() or "imported"

    def clamp(s: str) -> str:
        return s[:max_len] if max_len and len(s) > max_len else s

    candidate = clamp(base)
    if candidate not in used:
        return candidate
    # Reserve room for the suffix when trimming to max_len.
    for n in range(1, 10_000):
        suffix = "_imported" if n == 1 else f"_imported_{n}"
        head = base
        if max_len and len(head) + len(suffix) > max_len:
            head = head[: max_len - len(suffix)]
        candidate = head + suffix
        if candidate not in used:
            return candidate
    return f"{base}_{len(used)}"  # pathological fallback; effectively never reached


def _sanitize_component_name(name: str) -> str:
    """Coerce a name to the component identifier charset (used verbatim as the LLM tool name)."""
    cleaned = _COMPONENT_NAME_RE.sub("_", (name or "").strip())[:_COMPONENT_NAME_MAX]
    return cleaned or "component"


def _deep_remap(node: Any, id_map: dict[str, str]) -> Any:
    """Return `node` with every string that is a key in `id_map` replaced by its mapped id
    (recursing through dicts and lists). Ids are UUIDs, so only genuine references match."""
    if isinstance(node, dict):
        return {k: _deep_remap(v, id_map) for k, v in node.items()}
    if isinstance(node, list):
        return [_deep_remap(v, id_map) for v in node]
    if isinstance(node, str):
        return id_map.get(node, node)
    return node


def _contains_secret_ref(node: Any) -> bool:
    if isinstance(node, str):
        return "secret://" in node
    if isinstance(node, dict):
        return any(_contains_secret_ref(v) for v in node.values())
    if isinstance(node, list):
        return any(_contains_secret_ref(v) for v in node)
    return False


# --------------------------------------------------------------------------- service -------


class PortabilityService:
    """Stateless export/import of a single entity type. See module docstring."""

    @staticmethod
    async def export(
        session: AsyncSession,
        tenant_id: str,
        project_id: str,
        entity_type: str,
        ids: list[str],
    ) -> dict:
        if entity_type not in EXPORTABLE:
            raise ValueError(f"unexportable type {entity_type!r}")
        project_name = (
            await session.execute(
                select(Project.name).where(
                    Project.tenant_id == tenant_id, Project.id == project_id
                )
            )
        ).scalar_one_or_none()
        model = _MODEL[entity_type]
        rows = (
            await session.execute(
                select(model).where(
                    model.tenant_id == tenant_id,
                    model.project_id == project_id,
                    model.id.in_(ids or []),
                )
            )
        ).scalars().all()
        by_id = {r.id: r for r in rows}
        # Preserve the caller's selection order; silently drop ids that aren't in this project.
        ordered = [by_id[i] for i in (ids or []) if i in by_id]
        exporter = _EXPORTERS[entity_type]
        return {
            "format": BUNDLE_FORMAT,
            "type": entity_type,
            "exported_at": datetime.now(UTC).isoformat(),
            "source": {"project_id": project_id, "project_name": project_name},
            "items": [jsonable(exporter(r)) for r in ordered],
        }

    @staticmethod
    async def import_bundle(
        session: AsyncSession,
        tenant_id: str,
        project_id: str,
        bundle: dict,
        *,
        author: Any | None = None,
    ) -> dict:
        entity_type = bundle.get("type")
        if entity_type not in EXPORTABLE:
            raise ValueError(
                f"Unknown or missing bundle type {entity_type!r}. Expected one of {', '.join(EXPORTABLE)}."
            )
        items = bundle.get("items") or []
        if not isinstance(items, list):
            raise ValueError("Bundle 'items' must be a list.")

        model = _MODEL[entity_type]
        # Names already taken in the target project (for auto-rename).
        used: set[str] = set(
            (
                await session.execute(
                    select(model.name).where(
                        model.tenant_id == tenant_id, model.project_id == project_id
                    )
                )
            ).scalars().all()
        )
        # Auth providers that actually exist in the target project (tool imports only).
        valid_auth: set[str] | None = None
        if entity_type == "tool":
            valid_auth = set(
                (
                    await session.execute(
                        select(AuthProvider.id).where(
                            AuthProvider.tenant_id == tenant_id,
                            AuthProvider.project_id == project_id,
                        )
                    )
                ).scalars().all()
            )

        id_map: dict[str, str] = {}
        created: list[Any] = []
        report_items: list[dict] = []
        warnings: list[str] = []
        skipped = 0

        creator = getattr(PortabilityService, f"_create_{entity_type}")
        for item in items:
            if not isinstance(item, dict):
                skipped += 1
                warnings.append("Skipped a malformed item (not an object).")
                continue
            orig_name = str(item.get("name") or entity_type)
            max_len = _COMPONENT_NAME_MAX if entity_type == "component" else None
            desired = _sanitize_component_name(orig_name) if entity_type == "component" else orig_name
            name = _unique_name(desired, used, max_len=max_len)
            obj, warns = await creator(
                session, tenant_id, project_id, item, name, author, valid_auth
            )
            warnings.extend(warns)
            if obj is None:
                skipped += 1
                report_items.append({"original_name": orig_name, "skipped": True})
                continue
            used.add(name)
            old_id = item.get("id")
            if isinstance(old_id, str) and old_id:
                id_map[old_id] = obj.id
            created.append(obj)
            report_items.append(
                {
                    "id": obj.id,
                    "name": name,
                    "original_name": orig_name,
                    "renamed": name != orig_name,
                }
            )

        # PASS 2: rewrite any reference to an id that was in this bundle to its new id.
        if id_map:
            for obj in created:
                PortabilityService._remap(obj, id_map, entity_type)
            await session.commit()

        # Record a version-history snapshot for each import, exactly like create/update does.
        for obj in created:
            await safe_snapshot(session, entity_type, obj, author=author)

        return {
            "type": entity_type,
            "imported": len(created),
            "skipped": skipped,
            "items": report_items,
            "warnings": warnings,
        }

    # ----- per-type create paths (return (obj|None, warnings)) -----

    @staticmethod
    async def _create_tool(session, tenant_id, project_id, item, name, author, valid_auth):
        warns: list[str] = []
        kind = str(item.get("kind") or "rest_api")
        cfg = dict(item.get("config") or {})
        cfg.pop("_last_test", None)

        def resolve_ap(value):
            if value and valid_auth is not None and value not in valid_auth:
                return None
            return value or None

        ap = resolve_ap(item.get("auth_provider_id"))
        if item.get("auth_provider_id") and ap is None:
            warns.append(
                f"Tool '{name}': its auth provider isn't in this project — cleared it. Re-attach an auth provider."
            )
        # The config copy can also embed an auth_provider_id; keep it consistent.
        if cfg.get("auth_provider_id") and resolve_ap(cfg.get("auth_provider_id")) is None:
            cfg["auth_provider_id"] = None
        if _contains_secret_ref(cfg):
            warns.append(
                f"Tool '{name}' references secrets (secret://…); create those secrets in this project for it to run."
            )
        # Same validation the create route applies, so an imported tool is as valid as a built one.
        errors = validate_against_id({**cfg, "name": name, "kind": kind}, "forge/tool")
        if errors:
            warns.append(f"Tool '{name}' failed validation and was skipped ({len(errors)} error(s)).")
            return None, warns
        tool = await ToolService.create(
            session, tenant_id, project_id, name=name, kind=kind, config=cfg, auth_provider_id=ap
        )
        return tool, warns

    @staticmethod
    async def _create_component(session, tenant_id, project_id, item, name, author, _valid_auth):
        comp = await ComponentService.create(
            session,
            tenant_id,
            project_id,
            name=name,
            title=item.get("title"),
            description=item.get("description") or "",
            props_schema=item.get("props_schema") or {},
            html=item.get("html") or "",
            css=item.get("css") or "",
            actions=item.get("actions") or [],
            sample_props=item.get("sample_props") or {},
            kind=item.get("kind") or "html",
        )
        # ComponentService.create doesn't take `enabled`; honor an exported disabled state.
        if item.get("enabled") is False:
            comp.enabled = False
            await session.commit()
            await session.refresh(comp)
        return comp, []

    @staticmethod
    async def _create_agent(session, tenant_id, project_id, item, name, author, _valid_auth):
        agent = await AgentService.create(
            session,
            tenant_id,
            project_id,
            name=name,
            config=item.get("config") or {},
            created_by=getattr(author, "id", None),
            created_by_email=getattr(author, "email", None),
        )
        return agent, []

    @staticmethod
    async def _create_workflow(session, tenant_id, project_id, item, name, author, _valid_auth):
        wf = await WorkflowService.create(
            session,
            tenant_id,
            project_id,
            name=name,
            description=item.get("description"),
            executable=item.get("executable") or {},
            canvas=item.get("canvas") or {},
        )
        return wf, []

    # ----- reference remap -----

    @staticmethod
    def _remap(obj, id_map: dict[str, str], entity_type: str) -> None:
        if entity_type in ("tool", "agent"):
            obj.config = _deep_remap(obj.config or {}, id_map)
        elif entity_type == "workflow":
            obj.canvas = _deep_remap(obj.canvas or {}, id_map)
            obj.executable = _deep_remap(obj.executable or {}, id_map)
        # components hold no id references.
