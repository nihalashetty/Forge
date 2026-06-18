"""Component CRUD (Feature 2 — user-authored UI components).

Mirrors ToolService: tenant/project-scoped CRUD over the `components` table. The
component's HTML/CSS template and declarative button `actions` are stored as-is;
`props_schema` (JSON Schema) describes the props the agent must supply when it
renders the component. Rendering is client-side, so there is no server-side
execute/test — the editor previews with `sample_props` in the browser.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.models import Component


class ComponentService:
    @staticmethod
    async def list(session: AsyncSession, tenant_id: str, project_id: str) -> list[Component]:
        rows = await session.execute(
            select(Component).where(Component.tenant_id == tenant_id, Component.project_id == project_id)
        )
        return list(rows.scalars())

    @staticmethod
    async def get(session: AsyncSession, tenant_id: str, project_id: str, component_id: str) -> Component | None:
        # Scope by project too (not just tenant) so the project_id path segment is load-bearing
        # and one tenant's project can't read/mutate another's component (audit L2/F29).
        row = await session.execute(
            select(Component).where(
                Component.tenant_id == tenant_id, Component.project_id == project_id, Component.id == component_id
            )
        )
        return row.scalar_one_or_none()

    @staticmethod
    async def create(
        session: AsyncSession, tenant_id: str, project_id: str, *, name: str,
        title: str | None = None, description: str = "", props_schema: dict | None = None,
        html: str = "", css: str = "", actions: list | None = None,
        sample_props: dict | None = None, kind: str = "html",
    ) -> Component:
        comp = Component(
            tenant_id=tenant_id, project_id=project_id, name=name, title=title,
            description=description or "", props_schema=props_schema or {}, html=html or "",
            css=css or "", actions=actions or [], sample_props=sample_props or {}, kind=kind or "html",
        )
        session.add(comp)
        await session.commit()
        await session.refresh(comp)
        return comp

    @staticmethod
    async def update(
        session: AsyncSession, comp: Component, *, name: str | None = None,
        title: str | None = None, description: str | None = None, props_schema: dict | None = None,
        html: str | None = None, css: str | None = None, actions: list | None = None,
        sample_props: dict | None = None, enabled: bool | None = None,
    ) -> Component:
        changed_template = False
        if name is not None:
            comp.name = name
        if title is not None:
            comp.title = title
        if description is not None:
            comp.description = description
        if props_schema is not None and props_schema != comp.props_schema:
            comp.props_schema = props_schema
            changed_template = True
        if html is not None and html != comp.html:
            comp.html = html
            changed_template = True
        if css is not None and css != comp.css:
            comp.css = css
            changed_template = True
        if actions is not None and actions != comp.actions:
            comp.actions = actions
            changed_template = True
        if sample_props is not None:
            comp.sample_props = sample_props
        if enabled is not None:
            comp.enabled = enabled
        # Bump version only when a RENDER-affecting field actually changed, so the client's
        # id@version cache isn't needlessly busted by metadata-only / no-op patches (audit F13).
        if changed_template:
            comp.version += 1
        await session.commit()
        await session.refresh(comp)
        return comp

    @staticmethod
    async def delete(session: AsyncSession, comp: Component) -> None:
        """Delete a component. Agents reference components by id in config["components"];
        components_for() skips missing ids, so deletion is safe for live agents."""
        await session.delete(comp)
        await session.commit()
