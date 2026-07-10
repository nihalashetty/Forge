"""Token-template rendering for auth recipes.

Supports `{{a.b}}` references resolved against a vars dict, e.g.
`{{cred.username}}`, `{{ctx.csrf}}`, `{{extracted.session}}`. Non-string leaves
pass through; whole-string matches preserve the resolved value's native type.

`render_value` walks a parsed JSON structure (dict/list) and additionally honors a
`{"$each": "{{input.rows}}", "$as": "row", "$do": {...}}` loop directive, so a JSON body
template can build a variable-length array from one list-valued arg (see `_render_each`).
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


def _sub_one(mm: "re.Match", vars: dict) -> str:
    # Embedded token (not a whole-string match): stringify the resolved value. Only a missing
    # value (None) becomes empty - a falsy-but-real value like 0 or False must render as "0"/
    # "False", not "" (an `x or ""` here would silently drop legitimate zeros/booleans).
    v = _lookup(mm.group(1), vars)
    return "" if v is None else str(v)


def render_template(s: str, vars: dict) -> Any:
    # Whole-string single token -> preserve native type (numbers, objects).
    m = _TOKEN.fullmatch(s.strip())
    if m:
        return _lookup(m.group(1), vars)
    return _TOKEN.sub(lambda mm: _sub_one(mm, vars), s)


def has_each_directive(obj: Any) -> bool:
    """True if `obj` (a parsed JSON structure) contains a `$each` loop directive anywhere - i.e.
    a dict that has "$each" as a KEY. Used to decide whether a body template needs structural
    rendering; a literal "$each" appearing inside a string value is NOT a directive and must not
    trigger it (that would silently change type coercion for unrelated templates)."""
    if isinstance(obj, dict):
        if "$each" in obj:
            return True
        return any(has_each_directive(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_each_directive(v) for v in obj)
    return False


def render_value(obj: Any, vars: dict, *, allow_each: bool = False) -> Any:
    """Walk a parsed JSON structure, rendering `{{token}}` leaves. `$each` loop directives are
    honored ONLY when `allow_each=True` (the REST body-template path opts in); every other caller
    - auth token_fetch/extract rules, data-node payloads - passes the default False, so a literal
    object key named "$each" stays an ordinary key instead of being reinterpreted as a loop."""
    if isinstance(obj, str):
        return render_template(obj, vars)
    if isinstance(obj, dict):
        if allow_each and "$each" in obj:
            return _render_each(obj, vars)
        return {k: render_value(v, vars, allow_each=allow_each) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_value(v, vars, allow_each=allow_each) for v in obj]
    return obj


def _render_each(directive: dict, vars: dict) -> list:
    """Expand a `{"$each": "{{input.rows}}", "$as": "row", "$do": {...}}` loop directive into a
    list: render `$do` once per item of the array `$each` resolves to, with the item bound under
    the `$as` name (default "item"). Outer vars (input/ctx/state) stay visible inside the loop, so
    a nested template can still read e.g. `{{input.quoteId}}`. A missing/None `$each` yields [];
    a single non-list value is treated as one item.

    This lets a JSON body template build a variable-length array (e.g. one productRow per edited
    cell) WITHOUT string-concatenating JSON - so the output is always valid JSON with native types
    preserved, and one tool call can carry many rows instead of one call per row.
    """
    each = directive.get("$each")
    seq = render_value(each, vars) if isinstance(each, str) else each
    if seq is None:
        items: list = []
    elif isinstance(seq, list):
        items = seq
    else:
        items = [seq]
    as_name = directive.get("$as") or "item"
    body = directive.get("$do")
    # allow_each=True so a `$do` body can itself contain a nested `$each` (loops within loops).
    return [render_value(body, {**vars, as_name: item}, allow_each=True) for item in items]
