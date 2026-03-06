"""Tests for clayde.telemetry."""

import json

from opentelemetry.sdk.trace import TracerProvider

from clayde.telemetry import FileSpanExporter, init_tracer


class TestInitTracer:
    def test_returns_tracer_provider(self, tmp_path):
        traces_file = str(tmp_path / "traces.jsonl")
        provider = init_tracer(traces_file=traces_file)
        assert isinstance(provider, TracerProvider)

    def test_creates_traces_file_on_export(self, tmp_path):
        traces_file = str(tmp_path / "traces.jsonl")
        provider = init_tracer(traces_file=traces_file)
        tracer = provider.get_tracer("clayde")
        with tracer.start_as_current_span("test"):
            pass
        provider.force_flush()
        assert (tmp_path / "traces.jsonl").exists()


class TestFileSpanExporter:
    def test_creates_directory(self, tmp_path):
        file_path = str(tmp_path / "subdir" / "traces.jsonl")
        FileSpanExporter(file_path)
        assert (tmp_path / "subdir").is_dir()

    def test_exports_spans_to_file(self, tmp_path):
        traces_file = str(tmp_path / "traces.jsonl")
        provider = init_tracer(traces_file=traces_file)
        tracer = provider.get_tracer("clayde")
        with tracer.start_as_current_span("test.span") as span:
            span.set_attribute("test.key", "test_value")
        provider.force_flush()

        lines = open(traces_file).readlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["name"] == "test.span"
        assert data["attributes"]["test.key"] == "test_value"

    def test_exports_nested_spans(self, tmp_path):
        traces_file = str(tmp_path / "traces.jsonl")
        provider = init_tracer(traces_file=traces_file)
        tracer = provider.get_tracer("clayde")
        with tracer.start_as_current_span("parent") as parent_span:
            parent_span.set_attribute("level", "parent")
            with tracer.start_as_current_span("child") as child_span:
                child_span.set_attribute("level", "child")
        provider.force_flush()

        lines = open(traces_file).readlines()
        assert len(lines) == 2
        spans = [json.loads(line) for line in lines]
        names = {s["name"] for s in spans}
        assert names == {"parent", "child"}

        child = next(s for s in spans if s["name"] == "child")
        parent = next(s for s in spans if s["name"] == "parent")
        assert child["parent_span_id"] == parent["span_id"]
        assert child["trace_id"] == parent["trace_id"]

    def test_exports_span_events(self, tmp_path):
        traces_file = str(tmp_path / "traces.jsonl")
        provider = init_tracer(traces_file=traces_file)
        tracer = provider.get_tracer("clayde")
        with tracer.start_as_current_span("test.span") as span:
            span.add_event("state_transition", attributes={
                "old_status": "(none)",
                "new_status": "planning",
            })
        provider.force_flush()

        lines = open(traces_file).readlines()
        data = json.loads(lines[0])
        assert len(data["events"]) == 1
        assert data["events"][0]["name"] == "state_transition"
        assert data["events"][0]["attributes"]["new_status"] == "planning"

    def test_records_duration(self, tmp_path):
        traces_file = str(tmp_path / "traces.jsonl")
        provider = init_tracer(traces_file=traces_file)
        tracer = provider.get_tracer("clayde")
        with tracer.start_as_current_span("test.span"):
            pass
        provider.force_flush()

        data = json.loads(open(traces_file).readline())
        assert data["duration_ms"] is not None
        assert data["duration_ms"] >= 0

    def test_force_flush_returns_true(self, tmp_path):
        file_path = str(tmp_path / "traces.jsonl")
        exporter = FileSpanExporter(file_path)
        assert exporter.force_flush() is True
