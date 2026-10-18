/** Read-only session history contract. No business writes, retries, or browser persistence. */
import type { AgentRun } from "./agentTransport";
export const HISTORY_PAGE_SIZE = 12;
export type HistoryLane = "history" | "attention";
export type HistoryRun = AgentRun & { created: number; session_id: string };
export type HistoryPage = { id: string; title: string; lane: HistoryLane; runs: HistoryRun[]; next_cursor: string | null; remaining: number; has_active: boolean };
const invalid = () => new Error("历史记录暂时无法读取，请重新打开会话；不会自动提交任何操作。");
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
export function historyPath(sid: string, lane: HistoryLane, cursor: string | null = null): string {
  const q = new URLSearchParams({ lane, limit: String(HISTORY_PAGE_SIZE), summary_only: "true" });
  if (cursor !== null) q.set("cursor", cursor);
  return `/sessions/${encodeURIComponent(sid)}/history?${q}`;
}
export function compareRuns(a: { created: number; id: string }, b: { created: number; id: string }): number {
  return a.created - b.created || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
}
export function historyPage(value: unknown, sid: string, lane: HistoryLane, cursor: string | null = null): HistoryPage {
  if (!record(value) || value.id !== sid || value.lane !== lane || typeof value.title !== "string" ||
      typeof value.has_active !== "boolean" || !Number.isSafeInteger(value.remaining) || Number(value.remaining) < 0 ||
      !Array.isArray(value.runs) || value.runs.length > HISTORY_PAGE_SIZE ||
      !(value.next_cursor === null || (typeof value.next_cursor === "string" && /^[A-Za-z0-9_-]{1,512}$/.test(value.next_cursor))) ||
      (value.next_cursor !== null && (value.next_cursor === cursor || value.runs.length === 0)) ||
      ((value.next_cursor === null) !== (value.remaining === 0))) throw invalid();
  const ids = new Set<string>();
  let prior: HistoryRun | undefined;
  for (const item of value.runs) {
    if (!record(item) || typeof item.id !== "string" || !item.id || ids.has(item.id) || item.session_id !== sid ||
        typeof item.created !== "number" || !Number.isFinite(item.created) || typeof item.input !== "string" ||
        typeof item.provider !== "string" || typeof item.status !== "string" || !item.status || item.status.length > 64 || !Array.isArray(item.events) ||
        (item.events_complete !== undefined && typeof item.events_complete !== "boolean")) throw invalid();
    if (lane === "attention" && ["COMPLETED", "FAILED", "STOPPED"].includes(item.status)) throw invalid();
    const run = item as HistoryRun;
    if (prior && compareRuns(prior, run) >= 0) throw invalid();
    let last = 0;
    for (const e of run.events) {
      if (!record(e) || !Number.isSafeInteger(e.id) || Number(e.id) <= last || typeof e.kind !== "string" || !record(e.data)) throw invalid();
      last = Number(e.id);
    }
    ids.add(run.id); prior = run;
  }
  return value as HistoryPage;
}
/** Keep fresher locally streamed or confirmed records when overlapping pages arrive late. */
export function mergeHistory(current: readonly AgentRun[], page: readonly HistoryRun[]): AgentRun[] {
  const map = new Map(current.map(run => [run.id, run]));
  for (const run of page) {
    const old = map.get(run.id);
    if (!old || (run.events.at(-1)?.id || 0) > (old.events.at(-1)?.id || 0)) map.set(run.id, run);
  }
  return [...map.values()].sort((a, b) => compareRuns(
    { id: a.id, created: (a as Partial<HistoryRun>).created ?? Number.MAX_SAFE_INTEGER },
    { id: b.id, created: (b as Partial<HistoryRun>).created ?? Number.MAX_SAFE_INTEGER },
  ));
}
