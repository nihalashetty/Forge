"use client";
/* Model picker data. The catalog (chat + embedding + reranker) is served by the backend
   (GET /v1/models) from its canonical lists, so no model dropdown hardcodes options in the
   frontend and the picker can only offer models the backend actually runs (and, for chat,
   prices). See forge/model_catalog.py. */
import { useEffect, useState } from "react";
import { api, type EmbeddingModelInfo, type ModelCatalog, type ModelInfo, type RerankerModelInfo } from "./api";

const EMPTY: ModelCatalog = { chat: [], embedding: [], reranker: [] };

// Fetched once, shared across every picker. api.json() also de-dupes concurrent GETs, so even
// a cold cache is a single round-trip.
let _cache: ModelCatalog | null = null;

function useModelCatalog(): ModelCatalog {
  const [catalog, setCatalog] = useState<ModelCatalog>(() => _cache ?? EMPTY);
  useEffect(() => {
    if (_cache) return;
    let alive = true;
    api
      .listModels()
      .then((c) => {
        _cache = c;
        if (alive) setCatalog(c);
      })
      .catch(() => {
        /* leave empty: selects still show the current value + any hardcoded default option */
      });
    return () => {
      alive = false;
    };
  }, []);
  return catalog;
}

export function useModels(): ModelInfo[] {
  return useModelCatalog().chat;
}

export function useEmbeddingModels(): EmbeddingModelInfo[] {
  return useModelCatalog().embedding;
}

export function useRerankerModels(): RerankerModelInfo[] {
  return useModelCatalog().reranker;
}
