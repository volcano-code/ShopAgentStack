# M1.1 — Agent tracing integration

This change targets ShopAgentStack baseline `d2f57756672629492d194df3a5a03331b72bf37e`.
It implements an opt-in Agent tracing boundary, not a full production observability stack.

## Implemented data flow

```text
POST /sessions/{sid}/runs (HTTP span + X-Trace-ID)
  └─ asyncio.create_task: agent.run (continues after POST returns)
       ├─ providers.complete: llm.call (includes streamed response consumption)
       └─ tools.call: tool.call
            └─ dedicated internal HTTPX request hook: W3C traceparent
GET /runs/{rid}/events: a separate HTTP span, open through the complete SSE response
```

`app.py` owns the SDK through lifespan. Pending run tasks are snapshotted, cancelled and
drained before exporter shutdown. Context is task-local rather than a process-global
trace provider. The server reads the final stored run status: a coroutine that catches
an error and returns normally is not automatically labelled successful.

The tracing implementation ships inside `shop_agent_stack`, so the existing Agent Docker
build context can copy it. `tools/shop_observability/telemetry.py` is now a compatibility
import of the canonical service implementation, not a second implementation.

## Enable locally

Default behavior is unchanged: `SHOP_AGENT_STACK_OTEL_ENABLED=false` and the standard
image does not install tracing dependencies. Build an explicitly enabled image:

```bash
docker build --build-arg SHOP_AGENT_STACK_WITH_OTEL=true \
  -t shop-agent-stack-agent:otel services/agent
```

Configure the existing Agent service environment (for example through the deployment's
Compose override); merely building a new tag does not replace an already running service:

```dotenv
SHOP_AGENT_STACK_OTEL_ENABLED=true
SHOP_AGENT_STACK_OTEL_EXPORTER=console
SHOP_AGENT_STACK_OTEL_SAMPLE_RATIO=1
```

Use full sampling only for a controlled demo. For an operator-managed OTLP collector:

```dotenv
SHOP_AGENT_STACK_OTEL_ENABLED=true
SHOP_AGENT_STACK_OTEL_EXPORTER=otlp
SHOP_AGENT_STACK_OTEL_SAMPLE_RATIO=0.1
SHOP_AGENT_STACK_OTEL_TRACES_ENDPOINT=http://otel-collector:4318/v1/traces
```

The endpoint is operator configuration, never a browser/LLM field. The HTTP exporter uses
an explicit session with environment proxies disabled. It is not a collector installer.
No public dashboard, Java instrumentation, Milvus spans, MQ metrics, or deployment is
created by these files. Enabled tracing with missing dependencies or invalid configuration
fails at startup rather than silently appearing to work.

`requirements-observability.txt` pins the optional tracing additions. It is not a newly
resolved whole-Agent lock. CI installs it together with the original `requirements.lock`
and runs `pip check`; the combined target image still requires execution in CI.

## Privacy and authority

Only fixed operation/tool/provider labels, validated numeric usage, HTTP method/status,
run state, and trace/span identifiers are emitted. No model name supplied by a customer,
request path/query, prompts, answers, reasoning, model keys, business IDs, cookies,
authorization/execution grants, raw exception messages, or raw tool payloads are recorded.
Missing usage is unknown, not zero; fixture engine spans are explicitly labelled fixtures.
Arbitrary `OTEL_RESOURCE_ATTRIBUTES` are not imported into the resource.

Incoming public `traceparent`/`tracestate`/baggage is not trusted. Only the dedicated internal
MCP HTTP client receives an outgoing traceparent. It is injected per request under the
current tool span; the external LLM client is not instrumented with those headers.
These diagnostic identifiers are never used as authorization.

The model tool allowlist is unchanged; `submit_after_sale` remains outside MODEL_TOOLS.
Observation labels do not grant execution permissions. Confirmation, stop/reconcile,
owner checks, pricing, and Java transaction logic are not reimplemented or relaxed.

SSE reconnects currently create a new HTTP trace. This version does not persist trace IDs
in the run store or link every replay to the original run trace. Sampled-out requests can
still have an X-Trace-ID without a corresponding exported trace.

## Tests and evidence boundaries

The offline integration tests use the real changed application/provider/tool-adapter
source, FastAPI, SQLite Store, HTTPX and OpenTelemetry SDK. Java identity/configuration,
the LangGraph worker, MCP SDK transport and collector HTTP peer are explicit doubles.
They prove these component contracts, not full Agent task success, real-model accuracy,
MCP wire compatibility, Java transaction correctness, or collector availability.

The source verification bundle records Git blob SHA matches for selected upstream files.
It is not a complete checkout. Local versions (Python/FastAPI/etc.) differ from the target
Docker lock. Full original regression, both Docker build variants, hosted GitHub Actions,
real model/RAG experiments and deployment remain separate acceptance steps.

```bash
# Focused checks with the optional dependencies installed; no paid model calls.
PYTHONPATH=services/agent python -m pytest \
  services/agent/tests/test_providers.py \
  services/agent/tests/test_observability_disabled.py \
  services/agent/tests/test_observability_integration.py \
  tools/shop_quality/tests tools/shop_observability/tests -q
```

The CI Agent job now covers both optional-image settings (`false` and `true`) with no
outbound network during tests. The SDK-specific tests explicitly skip in a default image
without the SDK; they must execute in the enabled image. CI remains a candidate until a
real hosted run completes. The M1 fixture evaluator is still a software test, not a model
benchmark. `docs/quality-m1.md` describes that earlier standalone milestone.
