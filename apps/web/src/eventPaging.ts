/** Owner-scoped read-only run-event pages. Never calls a mutation endpoint. */
import { eventValue, runValue, requestAgent, type AgentRun, type RunEvent } from "./agentTransport.ts";
export const EVENT_PAGE_LIMIT = 100;
export const EVENT_PAGE_BYTE_LIMIT = 192 * 1024;

type EventPage = { events: RunEvent[]; next_after: number; has_more: boolean };
const invalid = () => new Error("历史进度页格式异常，请重试读取，不会提交业务操作。");
function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
export function eventPage(value: unknown, after: number): EventPage {
  if (!record(value) || !Array.isArray(value.events) ||
      value.events.length > EVENT_PAGE_LIMIT || typeof value.has_more !== "boolean" ||
      !Number.isSafeInteger(value.next_after) || Number(value.next_after) < after ||
      (value.events.length === 0 && value.has_more)) throw invalid();
  // Defend against invalid/injected transport responses before updating UI state.
  // The server applies the same byte cap before serializing JSON.
  if (new TextEncoder().encode(JSON.stringify(value)).byteLength > EVENT_PAGE_BYTE_LIMIT) throw invalid();
  let last = after;
  for (const candidate of value.events) {
    let event: RunEvent;
    try { event = eventValue(candidate); } catch { throw invalid(); }
    if (event.id <= last) throw invalid();
    last = event.id;
  }
  if (Number(value.next_after) !== last) throw invalid();
  return value as EventPage;
}
/** Page appends preserve streamed/confirmed newer state and deduplicate event IDs. */
export function mergeEventPage(run: AgentRun, page: EventPage): AgentRun {
  const mostRecent = run.events.reduce((n, e) => Math.max(n, e.id), 0);
  const merged = new Map(run.events.map(e => [e.id, e]));
  for (const event of page.events) if (!merged.has(event.id)) merged.set(event.id, event);
  const events = [...merged.values()].sort((a, b) => a.id - b.id);
  const candidate = page.events.at(-1);
  const status = candidate && candidate.id > mostRecent && candidate.kind === "state" &&
    typeof candidate.data.status === "string" ? candidate.data.status : run.status;
  return { ...run, status, events, events_after: page.next_after, events_complete: !page.has_more };
}
export function eventPagePath(rid: string, after = 0): string {
  return `/runs/${encodeURIComponent(rid)}/events/page?after=${after}&limit=${EVENT_PAGE_LIMIT}`;
}

/** Explicit manual verification after an uncertain mutation, never a retry of the write.
 * Bound cumulative memory, time, and calls. If the run is too long, fail
 * without publishing an incomplete operation/confirmation snapshot.
 */
export const MAX_MANUAL_RECOVERY_PAGES = 12;
export async function readRunComplete(
  id: string, authorization: string,
  options: { signal?: AbortSignal; fetcher?: typeof fetch; onUnauthorized?: () => void } = {},
): Promise<AgentRun> {
  const signal = AbortSignal.any([
    ...(options.signal ? [options.signal] : []), AbortSignal.timeout(30000),
  ]);
  const url = `/runs/${encodeURIComponent(id)}`;
  const header = runValue(await requestAgent<unknown>(url + "?summary_only=true", authorization,
    undefined, "GET", { ...options, signal }), id);
  if (header.events.length || header.events_complete !== false)
    throw new Error("服务器未返回只读运行摘要，无法安全核实。");
  const all: RunEvent[] = [];
  let cursor = 0;
  for (let count = 0; count < MAX_MANUAL_RECOVERY_PAGES; count++) {
    const page = eventPage(await requestAgent<unknown>(eventPagePath(id, cursor),
      authorization, undefined, "GET", { ...options, signal }), cursor);
    all.push(...page.events);
    cursor = page.next_after;
    if (!page.has_more) return { ...header, events: all, events_after: cursor, events_complete: true };
  }
  // Never expose a partial preview as a complete authoritative answer.
  throw new Error("历史记录超过本次核实读取上限，请保留原操作状态并继续核实。");
}
