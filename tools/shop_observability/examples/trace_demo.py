"""Actual SDK smoke demo with synthetic operations. No external LLM or shop backend."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from tools.shop_observability.telemetry import Telemetry
from tools.shop_quality.reporting import atomic_write


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "shop-observability-fixture"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    observer = Telemetry(provider.get_tracer("shop-quality-m1"), tools=frozenset({"search_policy"}),
                         providers=frozenset({"fixture"}), models=frozenset({"not-called"}))
    try:
        with observer.operation("agent.run"):
            with observer.operation("llm.call", attributes={"gen_ai.provider.name": "fixture", "gen_ai.request.model": "not-called"}):
                observer.record_usage(input_tokens=0, output_tokens=0)
            with observer.operation("tool.call", attributes={"shop.tool.name": "search_policy"}):
                with observer.operation("retrieval.search", attributes={"shop.retrieval.candidates": 20, "shop.retrieval.returned": 5}):
                    pass
            with observer.operation("source.validate", attributes={"shop.source.valid": True}):
                pass
        provider.force_flush()
        rows = [{"name": span.name, "trace_id": f"{span.context.trace_id:032x}",
                 "span_id": f"{span.context.span_id:016x}", "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
                 "attributes": dict(span.attributes), "duration_ms": (span.end_time-span.start_time)/1e6}
                for span in exporter.get_finished_spans()]
        atomic_write(args.out, json.dumps({"scope": "real_sdk_synthetic_operations_not_upstream_trace", "spans": rows}, indent=2) + "\n")
        print(f"{len(rows)} real SDK spans written; operations are synthetic, not calls to the shop or an LLM.")
    finally:
        provider.shutdown()


if __name__ == "__main__":
    main()
