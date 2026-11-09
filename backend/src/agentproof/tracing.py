"""OpenTelemetry tracing for runs.

Each run gets its own ``TracerProvider`` with an in-memory exporter, so spans can
be stored next to the results (and shown in the viewer) without any collector.
If ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set and the OTLP exporter is installed,
spans are also exported to that endpoint (Jaeger, Langfuse, Tempo, ...).

Attribute names follow the OpenTelemetry GenAI semantic conventions where one
exists (``gen_ai.*``) and use ``agentproof.*`` otherwise.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Tracer

log = logging.getLogger(__name__)

MAX_ATTR_CHARS = 4000


def attr_json(value: Any) -> str:
    """Serialize a value for a span attribute, truncated to keep spans small."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    if len(text) > MAX_ATTR_CHARS:
        return text[:MAX_ATTR_CHARS] + f"...[truncated {len(text) - MAX_ATTR_CHARS} chars]"
    return text


class TraceRecorder:
    def __init__(self, service_name: str = "agentproof", otlp_endpoint: str | None = None):
        self.provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        self.exporter = InMemorySpanExporter()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        endpoint = otlp_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            except ImportError:  # pragma: no cover - optional extra
                log.warning("OTEL_EXPORTER_OTLP_ENDPOINT is set but the 'otlp' extra is not installed")
            else:
                self.provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        self.tracer: Tracer = self.provider.get_tracer("agentproof")

    def drain(self) -> list[dict[str, Any]]:
        """Return every finished span as a plain dict and clear the buffer."""
        spans = [span_to_dict(span) for span in self.exporter.get_finished_spans()]
        self.exporter.clear()
        spans.sort(key=lambda s: (s["start_ns"], s["name"]))
        return spans

    def shutdown(self) -> None:
        self.provider.shutdown()


def span_to_dict(span: ReadableSpan) -> dict[str, Any]:
    ctx = span.get_span_context()
    parent = span.parent
    attributes = {
        key: (list(value) if isinstance(value, tuple) else value)
        for key, value in (span.attributes or {}).items()
    }
    start = span.start_time or 0
    end = span.end_time or start
    return {
        "span_id": f"{ctx.span_id:016x}",
        "trace_id": f"{ctx.trace_id:032x}",
        "parent_span_id": f"{parent.span_id:016x}" if parent is not None else None,
        "name": span.name,
        "kind": str(attributes.get("agentproof.kind", "internal")),
        "start_ns": start,
        "end_ns": end,
        "duration_ms": round((end - start) / 1e6, 3),
        "status": span.status.status_code.name,
        "attributes": attributes,
    }
