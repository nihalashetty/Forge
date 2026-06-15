"""Extract values from a token-fetch response (header / cookie / json path)."""

from __future__ import annotations

from typing import Any

import httpx


def _json_path(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            i = int(part)
            cur = cur[i] if 0 <= i < len(cur) else None
        else:
            return None
    return cur


def extract_value(resp: httpx.Response, rule: dict) -> Any:
    src = rule.get("from")
    if src == "header":
        return resp.headers.get(rule["header"])
    if src == "cookie":
        return resp.cookies.get(rule["cookie"])
    if src == "json":
        try:
            return _json_path(resp.json(), rule["json_path"])
        except Exception:  # noqa: BLE001 - non-JSON body
            return None
    return None
