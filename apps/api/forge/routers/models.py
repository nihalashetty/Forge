"""Model catalog - drives every model picker in the console (chat, embedding, reranker).

Served from `forge.model_catalog`, the same lists the built-in pricing rates derive from, so a
dropdown can only ever offer models the backend can actually run (and, for chat, price). This is
the single source of truth: the frontend hardcodes no model lists.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from forge.model_catalog import CHAT_MODELS, EMBEDDING_MODELS, RERANKER_MODELS

router = APIRouter(prefix="/v1/models", tags=["catalog"])


class ChatModelOut(BaseModel):
    id: str
    name: str
    provider: str
    ctx: str
    tools: bool
    vision: bool


class EmbeddingModelOut(BaseModel):
    id: str
    name: str
    provider: str
    dim: int
    billed: bool
    default: bool


class RerankerModelOut(BaseModel):
    id: str
    name: str
    note: str
    default: bool


class ModelCatalogOut(BaseModel):
    chat: list[ChatModelOut]
    embedding: list[EmbeddingModelOut]
    reranker: list[RerankerModelOut]


@router.get("", response_model=ModelCatalogOut)
async def list_models() -> ModelCatalogOut:
    return ModelCatalogOut(
        chat=[
            ChatModelOut(id=m.id, name=m.name, provider=m.provider, ctx=m.ctx, tools=m.tools, vision=m.vision)
            for m in CHAT_MODELS
        ],
        embedding=[
            EmbeddingModelOut(id=m.id, name=m.name, provider=m.provider, dim=m.dim, billed=m.billed, default=m.default)
            for m in EMBEDDING_MODELS
        ],
        reranker=[
            RerankerModelOut(id=m.id, name=m.name, note=m.note, default=m.default)
            for m in RERANKER_MODELS
        ],
    )
