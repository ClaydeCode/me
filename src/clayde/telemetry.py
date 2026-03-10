"""OpenTelemetry tracing setup for Clayde."""

import json
import logging
import os
from pathlib import Path
from typing import Sequence

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

log = logging.getLogger("clayde.telemetry")

_TRACES_FILE = str(
    Path(os.environ.get("CLAYDE_DIR", Path.cwd())) / "logs" / "traces.jsonl"
)


class FileSpanExporter(SpanExporter):
    """Append spans as JSONL to a file."""

    def __init__(self, file_path: str):
        self._file_path = Path(file_path)
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence) -> SpanExportResult:
        try:
            with open(self._file_path, "a") as f:
                for span in spans:
                    f.write(json.dumps(_span_to_dict(span)) + "\n")
            return SpanExportResult.SUCCESS
        except Exception:
            log.exception("Failed to export spans to file")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 0) -> bool:
        return True


def _span_to_dict(span) -> dict:
    ctx = span.get_span_context()
    parent = span.parent
    return {
        "name": span.name,
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(parent.span_id, "016x") if parent else None,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "duration_ms": (span.end_time - span.start_time) / 1_000_000 if span.end_time and span.start_time else None,
        "status": span.status.status_code.name if span.status else None,
        "attributes": dict(span.attributes) if span.attributes else {},
        "events": [
            {
                "name": e.name,
                "timestamp": e.timestamp,
                "attributes": dict(e.attributes) if e.attributes else {},
            }
            for e in span.events
        ],
    }


def init_tracer(traces_file: str | None = None) -> TracerProvider:
    """Initialize and set the global TracerProvider.

    Args:
        traces_file: Path to JSONL traces file. Defaults to logs/traces.jsonl.

    Returns:
        The configured TracerProvider.
    """
    resource = Resource.create({"service.name": "clayde"})
    provider = TracerProvider(resource=resource)

    file_path = traces_file or _TRACES_FILE
    provider.add_span_processor(SimpleSpanProcessor(FileSpanExporter(file_path)))

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            log.info("OTLP exporter configured for %s", endpoint)
        except Exception:
            log.warning("Failed to configure OTLP exporter — continuing with file exporter only")

    trace.set_tracer_provider(provider)
    return provider


def get_tracer() -> trace.Tracer:
    """Return the clayde tracer."""
    return trace.get_tracer("clayde")
