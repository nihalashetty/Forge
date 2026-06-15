"""Code-tool (RestrictedPython sandbox) and SQL-tool (read-only) tests."""

from __future__ import annotations

import pytest

from forge.services.runtime import make_runtime_ctx
from forge.tools.code import CodeToolError, execute_code, run_code
from forge.tools.materialize import materialize_tool
from forge.tools.sql import SqlToolError, execute_sql

# --- code tool ---


async def test_code_tool_main_returns_value():
    cfg = {"name": "adder", "kind": "code", "language": "python",
           "source": "def main(a, b):\n    return a + b\n",
           "args_schema": {"properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}}
    assert await execute_code(cfg, {"a": 2, "b": 5}) == 7


async def test_code_tool_allows_safe_import():
    assert await execute_code({"source": "import math\ndef main(x):\n    return math.sqrt(x)\n"}, {"x": 9}) == 3.0


async def test_code_tool_blocks_unsafe_import():
    with pytest.raises(CodeToolError):
        run_code("import os\ndef main():\n    return os.getcwd()\n", {})


async def test_code_tool_blocks_dunder_escape():
    with pytest.raises(CodeToolError):
        run_code("def main():\n    return ().__class__.__bases__\n", {})


async def test_code_tool_materializes_as_structured_tool():
    cfg = {"name": "upper", "kind": "code",
           "source": "def main(s):\n    return s.upper()\n",
           "args_schema": {"properties": {"s": {"type": "string"}}, "required": ["s"]}}
    tool = materialize_tool(cfg, make_runtime_ctx("t", "p"))
    assert await tool.ainvoke({"s": "hi"}) == "HI"


# --- sql tool (read-only) ---


async def test_sql_tool_rejects_writes():
    cfg = {"name": "q", "kind": "sql", "query": "DELETE FROM users", "connection_url": "sqlite+aiosqlite:///:memory:"}
    with pytest.raises(SqlToolError):
        await execute_sql(cfg, {}, tenant_id="t", project_id="p")


async def test_sql_tool_rejects_multi_statement():
    cfg = {"name": "q", "kind": "sql", "query": "SELECT 1; DROP TABLE users", "connection_url": "sqlite+aiosqlite:///:memory:"}
    with pytest.raises(SqlToolError):
        await execute_sql(cfg, {}, tenant_id="t", project_id="p")


async def test_sql_tool_reads_rows(tmp_path):
    import sqlite3

    db = tmp_path / "demo.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE t(id INTEGER, name TEXT); INSERT INTO t VALUES (1,'a'),(2,'b');")
    con.commit(); con.close()

    cfg = {"name": "q", "kind": "sql", "connection_url": f"sqlite+aiosqlite:///{db.as_posix()}",
           "query": "SELECT id, name FROM t WHERE id = :id",
           "args_schema": {"properties": {"id": {"type": "integer"}}, "required": ["id"]}}
    res = await execute_sql(cfg, {"id": 2}, tenant_id="t", project_id="p")
    assert res["rows"] == [{"id": 2, "name": "b"}]
