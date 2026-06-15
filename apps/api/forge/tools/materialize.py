"""Dispatch a tool config to a runnable StructuredTool by `kind`."""

from __future__ import annotations

from forge.tools.builtin import build_builtin_tool
from forge.tools.code import build_code_tool
from forge.tools.graphql import build_graphql_tool
from forge.tools.rest import build_rest_tool
from forge.tools.sql import build_sql_tool


def materialize_tool(cfg: dict, ctx):
    kind = cfg.get("kind")
    if kind == "rest_api":
        return build_rest_tool(cfg, ctx)
    if kind == "graphql":
        return build_graphql_tool(cfg, ctx)
    if kind == "builtin":
        return build_builtin_tool(cfg, ctx)
    if kind == "code":
        return build_code_tool(cfg, ctx)
    if kind == "sql":
        return build_sql_tool(cfg, ctx)
    raise ValueError(f"Unknown tool kind {kind!r}")
