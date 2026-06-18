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


def export(records, *, trace_name: str = "run") -> None:
    """Emit one OTel span per SpanRecord (flat; timing + GenAI attributes preserved)."""
    if not enabled() or not records:
        return
    try:
        for r in records:
            start_ns = int(getattr(r, "start", 0) * 1e9)
            end = getattr(r, "end", None) or getattr(r, "start", 0)
            span = _tracer.start_span(getattr(r, "name", trace_name), start_time=start_ns)
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
            span.end(end_time=int(end * 1e9))
    except Exception:  # noqa: BLE001 - tracing export must never break a run
        log.exception("OTel export failed")
