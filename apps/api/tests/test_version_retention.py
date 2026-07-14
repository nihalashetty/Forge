"""Version-history retention resolution (item 11).

The console Settings > Versioning panel writes `version_history_limit` into project.config;
snapshot()/prune must honor it, with precedence project.config > tenant.settings > global.
"""

from __future__ import annotations

import uuid

from forge.config import settings
from forge.db.base import SessionLocal
from forge.models import Project, Workflow
from forge.services.versions import VersionService, _limit_for


def test_limit_precedence():
    g = int(settings.version_history_limit)
    # project.config wins over tenant + global
    assert _limit_for({"version_history_limit": 7}, {"version_history_limit": 3}) == 3
    # tenant override used when the project sets none
    assert _limit_for({"version_history_limit": 7}, {}) == 7
    assert _limit_for({"version_history_limit": 7}, None) == 7
    # global default when neither is set
    assert _limit_for(None, None) == g
    # a non-integer value falls through to the next source
    assert _limit_for(None, {"version_history_limit": "nope"}) == g


async def test_project_config_limit_prunes_snapshots():
    t = f"t_{uuid.uuid4().hex[:8]}"
    async with SessionLocal() as s:
        proj = Project(tenant_id=t, name="P", slug=f"p{uuid.uuid4().hex[:6]}", config={"version_history_limit": 2})
        s.add(proj)
        await s.flush()
        wf = Workflow(tenant_id=t, project_id=proj.id, name="w", executable={}, status="draft")
        s.add(wf)
        await s.commit()
        pid, wid = proj.id, wf.id

    async with SessionLocal() as s:
        for i in range(5):
            await VersionService.snapshot(
                s, tenant_id=t, entity_type="workflow", entity_id=wid,
                data={"name": f"v{i}"}, project_id=pid,
            )
            await s.commit()

    async with SessionLocal() as s:
        rows = await VersionService.list(s, t, "workflow", wid)
        # project.config limit=2 -> only the two newest snapshots survive
        assert [r.version_no for r in rows] == [5, 4], [r.version_no for r in rows]
