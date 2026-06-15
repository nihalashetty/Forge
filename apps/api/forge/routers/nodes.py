"""Node-type catalog — drives the canvas palette + validation (from the registry)."""

from __future__ import annotations

from fastapi import APIRouter

import forge.nodes  # noqa: F401  (ensure registration)
from forge.engine.registry import all_specs
from forge.schemas.dto import NodeTypeOut, PortOut

router = APIRouter(prefix="/v1/node-types", tags=["catalog"])


def _ports(ports) -> list[PortOut]:
    return [
        PortOut(
            id=p.id, io_type=p.io_type, direction=p.direction,
            label=p.label, required=p.required, many=p.many,
        )
        for p in ports
    ]


@router.get("", response_model=list[NodeTypeOut])
async def list_node_types() -> list[NodeTypeOut]:
    return [
        NodeTypeOut(
            type=s.type,
            category=s.category,
            label=s.label or s.type,
            description=s.description,
            schema_id=s.schema_id,
            allows_cycle=s.allows_cycle,
            input_ports=_ports(s.input_ports),
            output_ports=_ports(s.output_ports),
        )
        for s in all_specs()
    ]
