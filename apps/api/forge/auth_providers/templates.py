"""Token-template rendering for auth recipes.

Supports `{{a.b}}` references resolved against a vars dict, e.g.
`{{cred.username}}`, `{{ctx.csrf}}`, `{{extracted.session}}`. Non-string leaves
pass through; whole-string matches preserve the resolved value's native type.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _lookup(path: str, vars: dict) -> Any:
    cur: Any = vars
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def render_template(s: str, vars: dict) -> Any:
    # Whole-string single token -> preserve native type (numbers, objects).
    m = _TOKEN.fullmatch(s.strip())
    if m:
        return _lookup(m.group(1), vars)
    return _TOKEN.sub(lambda mm: str(_lookup(mm.group(1), vars) or ""), s)


def render_value(obj: Any, vars: dict) -> Any:
    if isinstance(obj, str):
        return render_template(obj, vars)
    if isinstance(obj, dict):
        return {k: render_value(v, vars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_value(v, vars) for v in obj]
    return obj
