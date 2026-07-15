"""Regressions found by the full end-to-end application test (don't let them come back)."""

from __future__ import annotations

from forge.db.base import SessionLocal
from forge.models import Trigger
from forge.services.tools import ToolService
from forge.services.validation import validate_workflow
from forge.services.workflows import WorkflowService


def test_trigger_and_flow_nodes_validate():
    """node_schema_ref must resolve trigger/flow schemas whose file name != node type
    (e.g. webhook_in -> forge/nodes/trigger_webhook). Previously: Unresolvable schema."""
    wf = {
        "id": "w", "version": 1,
        "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
        "entry_node": "hook",
        "nodes": [
            {"id": "hook", "type": "webhook_in", "config": {"message_path": "text"}},
            {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:hi"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"source": "hook", "target": "agent"}, {"source": "agent", "target": "end"}],
    }
    res = validate_workflow(wf)
    assert res.valid, res.errors


async def test_tool_test_supports_code_and_sql(tmp_path, monkeypatch):
    import sqlite3

    from forge.config import settings
    # Code tools are OFF by default (unsandboxed RestrictedPython is not an isolation
    # boundary - audit S5); this test exercises the feature, so opt in explicitly.
    monkeypatch.setattr(settings, "enable_code_tools", True)
    code = {"name": "u", "kind": "code", "description": "upper", "language": "python",
            "source": "def main(s):\n    return s.upper()", "args_schema": {"properties": {"s": {"type": "string"}}, "required": ["s"]}}
    r = await ToolService.test("t", "p", code, {"s": "hi"})
    assert r["ok"] and r["projected"] == "HI"

    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE t(id int, name text); INSERT INTO t VALUES (1,'x');")
    con.commit()
    con.close()
    sql = {"name": "q", "kind": "sql", "description": "q", "connection_url": f"sqlite+aiosqlite:///{db.as_posix()}",
           "query": "SELECT name FROM t WHERE id = :id", "args_schema": {"properties": {"id": {"type": "integer"}}}}
    r = await ToolService.test("t", "p", sql, {"id": 1})
    assert r["ok"] and r["projected"] == [{"name": "x"}]


async def test_workflow_create_syncs_triggers():
    """Creating a workflow whose executable has a webhook_in must register a Trigger row."""
    ex = {
        "id": "w2", "version": 1,
        "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
        "entry_node": "hook",
        "nodes": [
            {"id": "hook", "type": "webhook_in", "config": {}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"source": "hook", "target": "end"}],
    }
    async with SessionLocal() as s:
        wf = await WorkflowService.create(s, "t_sync", "p_sync", name="Hooked", executable=ex)
        rows = (await s.execute(Trigger.__table__.select().where(Trigger.workflow_id == wf.id))).fetchall()
    assert len(rows) == 1 and rows[0].kind == "webhook_in" and rows[0].key
