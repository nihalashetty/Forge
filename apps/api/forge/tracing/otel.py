"""OpenTelemetry export of Forge run spans (GenAI semantic conventions).

When `FORGE_OTEL_ENABLED=true` (+ an OTLP endpoint), each run's spans are exported so
Forge interoperates with standard tracing backends (Jaeger, Tempo, Honeycomb) and
Langfuse (point its OTLP endpoint here). Best-effort: if the OTLP exporter package isn't
installed, export is a no-op. Tests inject an in-memory exporter via `configure`.
"""

from __future__ import annotations

import logging

from forge.config import settings

log = logging.getLogger("forge.otel")

_provider = None
_tracer = None
_configured = False


def configure(exporter=None) -> bool:
    """Set up the OTel tracer provider once. `exporter` overrides (tests pass an
    in-memory exporter). Returns True if export is active."""
    global _provider, _tracer, _configured
    _configured = True
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    except Exception:  # noqa: BLE001 - SDK missing
        log.warning("opentelemetry SDK not available; OTel export disabled")
        return False

    # A test-injected exporter (e.g. in-memory) needs SimpleSpanProcessor so spans are
    # visible synchronously; the real OTLP exporter uses BatchSpanProcessor so per-span
    # network export never blocks a run on the request path (audit P-imp).
    test_injected = exporter is not None
    if exporter is None:
        if not (settings.otel_enabled and settings.otel_exporter_otlp_endpoint):
            return False
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        except Exception as e:  # noqa: BLE001 - exporter package not installed
            log.warning("OTLP exporter unavailable (%s); install opentelemetry-exporter-otlp", e)
            return False

    _provider = TracerProvider(resource=Resource.create({"service.name": settings.otel_service_name}))
    processor = SimpleSpanProcessor(exporter) if test_injected else BatchSpanProcessor(exporter)
    _provider.add_span_processor(processor)
    _tracer = _provider.get_tracer("forge")
    return True


def enabled() -> bool:
    return _tracer is not None


def _provider_for(span_kind_model: str | None) -> str | None:
    if not span_kind_model:
        return None
    return span_kind_model.split(":", 1)[0] if ":" in span_kind_model else None


def _span_time_ns(r) -> tuple[int, int]:
    """(start_ns, end_ns) in WALL-CLOCK unix nanoseconds. SpanRecord.start/end are MONOTONIC
    (an arbitrary reference clock) - exporting those as epoch time landed every span near 1970
    and destroyed the waterfall. Prefer the wall-clock fields; fall back to now + latency so an
    older record still exports at a sane time rather than at the epoch."""
    import time

    start_wall = getattr(r, "start_wall", 0.0) or 0.0
    end_wall = getattr(r, "end_wall", None)
    if not start_wall:
        # No wall-clock captured: approximate from latency so ordering/duration survive.
        latency_s = (getattr(r, "latency_ms", 0) or 0) / 1000.0
        start_wall = time.time() - latency_s
        end_wall = time.time()
    if not end_wall:
        end_wall = start_wall + (getattr(r, "latency_ms", 0) or 0) / 1000.0
    return int(start_wall * 1e9), int(end_wall * 1e9)


def export(records, *, trace_name: str = "run") -> None:
    """Export a run's spans to OTel under ONE root span, preserving the parent/child hierarchy
    (so Jaeger/Tempo/Langfuse show a real waterfall grouped as a single trace) with correct
    wall-clock timestamps. Records arrive in creation order, so a parent always precedes its
    children and can be linked by context."""
    if not enabled() or not records:
        return
    try:
        from opentelemetry.trace import set_span_in_context

        starts = [t[0] for t in (_span_time_ns(r) for r in records)]
        root_start = min(starts) if starts else None
        ends = [_span_time_ns(r)[1] for r in records]
        root_end = max(ends) if ends else root_start
        root = _tracer.start_span(trace_name, start_time=root_start)
        root_ctx = set_span_in_context(root)
        ctx_by_id: dict = {}
        for r in records:
            start_ns, end_ns = _span_time_ns(r)
            parent_id = getattr(r, "parent_id", None)
            parent_ctx = ctx_by_id.get(parent_id, root_ctx) if parent_id else root_ctx
            span = _tracer.start_span(getattr(r, "name", trace_name), context=parent_ctx, start_time=start_ns)
            kind = getattr(r, "kind", None)
            model = getattr(r, "model", None)
            span.set_attribute("forge.kind", kind or "node")
            if model:
                span.set_attribute("gen_ai.request.model", model)
                provider = _provider_for(model)
                if provider:
                    span.set_attribute("gen_ai.system", provider)
            if getattr(r, "input_tokens", 0):
                span.set_attribute("gen_ai.usage.input_tokens", r.input_tokens)
            if getattr(r, "output_tokens", 0):
                span.set_attribute("gen_ai.usage.output_tokens", r.output_tokens)
            if getattr(r, "cost_usd", 0):
                span.set_attribute("forge.cost_usd", r.cost_usd)
            if getattr(r, "error", None):
                span.set_attribute("error", True)
                span.set_attribute("forge.error", str(r.error))
            span.end(end_time=end_ns)
            rid = getattr(r, "id", None)
            if rid:
                ctx_by_id[rid] = set_span_in_context(span)
        root.end(end_time=root_end)
    except Exception:  # noqa: BLE001 - tracing export must never break a run
        log.exception("OTel export failed")
