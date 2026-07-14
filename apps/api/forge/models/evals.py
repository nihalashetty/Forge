"""Eval history entities (finding F2): persisted eval runs + per-item results.

`Dataset` (in entities.py) only ever kept `last_pass_rate` - a single scalar with no
history and no per-example detail, so you couldn't diff two runs or see WHICH example
regressed. These two append-only tables record every eval run (timestamped, with a
rollup + a reference to the previous pass rate for the regression gate) and every
item's outcome (produced answer, pass/fail, numeric score, and the per-assertion
breakdown). Kept in a separate module so the append is isolated from the shared
entities.py (concurrent-edit safety); imported by forge.models so create_all registers them.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from forge.db.base import Base, PkTimestamp


class EvalRun(PkTimestamp, Base):
    """One execution of a dataset against its workflow. `created_at` (from PkTimestamp) is
    the timestamp; `prev_pass_rate`/`regressed` back the publish-time regression gate."""

    __tablename__ = "eval_runs"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    dataset_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    score_mode: Mapped[str] = mapped_column(String(20), default="contains")
    status: Mapped[str] = mapped_column(String(20), default="done")  # done|error
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    pass_rate: Mapped[float] = mapped_column(Float, default=0.0)
    # The dataset's pass rate BEFORE this run (the baseline the gate compares against); None
    # on the first ever run. `regressed` is set when the gate is on and the rate dropped.
    prev_pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    regressed: Mapped[bool] = mapped_column(Boolean, default=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)  # truncation, gate config, counts


class EvalResult(PkTimestamp, Base):
    """One dataset item's outcome within an EvalRun - the produced answer plus its score,
    so history + per-example diffing exist. `checks` holds the per-assertion breakdown when
    the item used an assertion list."""

    __tablename__ = "eval_results"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    eval_run_id: Mapped[str] = mapped_column(String(36), index=True)
    item_index: Mapped[int] = mapped_column(Integer, default=0)
    input: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # scored | run_failed | unavailable | error - so an inconclusive judge/embedding item is
    # distinguishable from a genuine fail (a misleading 0% is exactly what finding F4 fixes).
    status: Mapped[str] = mapped_column(String(20), default="scored")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    checks: Mapped[list] = mapped_column(JSON, default=list)
