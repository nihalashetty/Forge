"""An agent binds each tool NAME to the model at most once.

Two layers protect the model call: `resolve_tool_ids` de-dups by id (a tool that lives in several
tool sets is one record → sent once), and `_dedup_tools_by_name` is the final guard against a
name collision from distinct records/sources (tool names aren't unique per project, and the list
mixes tools + knowledge + MCP + components). Providers reject a duplicate function name.
"""

from __future__ import annotations

from types import SimpleNamespace

from forge.nodes.agent_node import _dedup_tools_by_name


def test_dedup_keeps_first_occurrence_by_name():
    first = SimpleNamespace(name="get_quote")
    dup = SimpleNamespace(name="get_quote")  # different object, same name (two Tool records)
    other = SimpleNamespace(name="create_order")
    out = _dedup_tools_by_name([first, other, dup])
    assert [t.name for t in out] == ["get_quote", "create_order"]
    assert out[0] is first  # the first occurrence wins


def test_dedup_never_drops_unnamed_tools():
    x = SimpleNamespace()  # no .name to key on
    y = SimpleNamespace()
    named = SimpleNamespace(name="t")
    out = _dedup_tools_by_name([x, named, y])
    assert len(out) == 3
