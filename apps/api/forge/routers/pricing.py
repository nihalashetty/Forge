"""Admin-editable model pricing (overlays the built-in defaults)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, get_session, require_role
from forge.models import ModelPrice
from forge.tracing.pricing import load_overrides, merged_prices, set_override

router = APIRouter(prefix="/v1/pricing", tags=["pricing"])


class PriceIn(BaseModel):
    input_per_1m: float
    output_per_1m: float


async def load_pricing_overrides(session) -> None:
    rows = (await session.execute(select(ModelPrice))).scalars()
    load_overrides({r.model: (r.input_per_1m, r.output_per_1m) for r in rows})


@router.get("")
async def list_pricing(_: CurrentUser = Depends(require_role("admin"))):
    return {m: {"input_per_1m": i, "output_per_1m": o} for m, (i, o) in sorted(merged_prices().items())}


@router.put("/{model}")
async def set_pricing(model: str, body: PriceIn, session: AsyncSession = Depends(get_session),
                      _: CurrentUser = Depends(require_role("admin"))):
    existing = (await session.execute(select(ModelPrice).where(ModelPrice.model == model))).scalar_one_or_none()
    if existing:
        existing.input_per_1m = body.input_per_1m
        existing.output_per_1m = body.output_per_1m
    else:
        session.add(ModelPrice(model=model, input_per_1m=body.input_per_1m, output_per_1m=body.output_per_1m))
    await session.commit()
    set_override(model, body.input_per_1m, body.output_per_1m)
    return {"model": model, "input_per_1m": body.input_per_1m, "output_per_1m": body.output_per_1m}
