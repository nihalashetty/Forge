"""UI component endpoints (CRUD) — Feature 2 (generative UI).

A Component is a saved HTML/CSS template + declarative button actions + a JSON-Schema
for its props. It is attached to agents like a tool (agent config["components"]) and
rendered client-side. DTOs are defined inline since the shape is self-contained.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import current_tenant_id, get_session
from forge.services.components import ComponentService

router = APIRouter(prefix="/v1/projects/{project_id}/components", tags=["components"])


class ComponentCreate(BaseModel):
    # Used verbatim as the LLM tool name → must match the provider-safe identifier charset
    # (audit M3), else the call fails at request time on real providers.
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    title: str | None = None
    description: str = ""
    props_schema: dict[str, Any] = Field(default_factory=dict)
    html: str = ""
    css: str = ""
    actions: list[dict[str, Any]] = Field(default_factory=list)
    sample_props: dict[str, Any] = Field(default_factory=dict)
    kind: str = "html"


class ComponentUpdate(BaseModel):
    name: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    title: str | None = None
    description: str | None = None
    props_schema: dict[str, Any] | None = None
    html: str | None = None
    css: str | None = None
    actions: list[dict[str, Any]] | None = None
    sample_props: dict[str, Any] | None = None
    enabled: bool | None = None


class ComponentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    title: str | None = None
    description: str = ""
    props_schema: dict[str, Any] = Field(default_factory=dict)
    html: str = ""
    css: str = ""
    actions: list[dict[str, Any]] = Field(default_factory=list)
    sample_props: dict[str, Any] = Field(default_factory=dict)
    kind: str = "html"
    enabled: bool = True
    version: int = 1


@router.get("", response_model=list[ComponentOut])
async def list_components(project_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    return await ComponentService.list(session, tenant_id, project_id)


@router.post("", response_model=ComponentOut, status_code=201)
async def create_component(project_id: str, body: ComponentCreate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    existing = await ComponentService.list(session, tenant_id, project_id)
    if any(c.name == body.name for c in existing):
        raise HTTPException(409, f"A component named '{body.name}' already exists in this project.")
    return await ComponentService.create(session, tenant_id, project_id, **body.model_dump())


@router.get("/{component_id}", response_model=ComponentOut)
async def get_component(project_id: str, component_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    comp = await ComponentService.get(session, tenant_id, project_id, component_id)
    if comp is None:
        raise HTTPException(404, "Component not found")
    return comp


@router.patch("/{component_id}", response_model=ComponentOut)
async def update_component(project_id: str, component_id: str, body: ComponentUpdate, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    comp = await ComponentService.get(session, tenant_id, project_id, component_id)
    if comp is None:
        raise HTTPException(404, "Component not found")
    if body.name and body.name != comp.name:
        existing = await ComponentService.list(session, tenant_id, project_id)
        if any(c.name == body.name and c.id != comp.id for c in existing):
            raise HTTPException(409, f"A component named '{body.name}' already exists in this project.")
    return await ComponentService.update(session, comp, **body.model_dump(exclude_unset=True))


@router.delete("/{component_id}", status_code=204)
async def delete_component(project_id: str, component_id: str, session: AsyncSession = Depends(get_session), tenant_id: str = Depends(current_tenant_id)):
    comp = await ComponentService.get(session, tenant_id, project_id, component_id)
    if comp is None:
        raise HTTPException(404, "Component not found")
    await ComponentService.delete(session, comp)
