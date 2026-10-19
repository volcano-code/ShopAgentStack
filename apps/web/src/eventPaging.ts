/** Owner-scoped read-only run-event pages. Never calls a mutation endpoint. */
import { eventValue, type AgentRun, type RunEvent } from "./agentTransport.ts";
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
