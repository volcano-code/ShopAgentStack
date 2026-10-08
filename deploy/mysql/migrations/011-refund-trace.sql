-- Optional diagnostics sidecar, committed with approval when tracing is enabled.
-- Not business intent, authorization, or the refund ledger. No payloads or baggage.
-- Safe to reapply. Missing table degrades tracing only, never refund processing.
CREATE TABLE IF NOT EXISTS shop_agent_stack_refund_trace (
 case_id BIGINT PRIMARY KEY,
 traceparent VARCHAR(55) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
);
