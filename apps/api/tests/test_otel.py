"""OpenTelemetry export emits GenAI-semconv spans from run SpanRecords."""

from __future__ import annotations

from forge.tracing import otel
from forge.tracing.tracer import SpanRecord


def test_export_emits_spans_with_genai_attributes():
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    assert otel.configure(exporter=exporter) is True
    assert otel.enabled() is True

    records = [
        SpanRecord(id="s1", parent_id=None, name="agent_1", kind="llm", start=1000.0, end=1000.5,
                   model="openai:gpt-4o-mini", input_tokens=120, output_tokens=30, cost_usd=0.0001),
        SpanRecord(id="s2", parent_id="s1", name="get_order", kind="tool", start=1000.5, end=1000.6, error="boom"),
    ]
    otel.export(records, trace_name="run")

    spans = exporter.get_finished_spans()
    assert len(spans) == 2
    by_name = {s.name: s for s in spans}
    llm = by_name["agent_1"]
    assert llm.attributes["gen_ai.request.model"] == "openai:gpt-4o-mini"
    assert llm.attributes["gen_ai.system"] == "openai"
    assert llm.attributes["gen_ai.usage.input_tokens"] == 120
    assert by_name["get_order"].attributes["error"] is True


def test_export_noop_when_unconfigured():
    # reset to unconfigured state
    otel._tracer = None
    otel.export([SpanRecord(id="x", parent_id=None, name="n", kind="node", start=0.0, end=1.0)])  # must not raise
