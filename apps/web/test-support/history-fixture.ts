/** Independent synthetic HTTP fixture. Real SQLite/API behavior is tested in Agent CI. */
import type { AgentRun } from "../src/agentTransport";
export function pagedFixture(id: string, title: string, runs: readonly AgentRun[], url: string) {
  const query = new URL(url).searchParams;
  const lane = query.get("lane") || "history";
  const limit = Number(query.get("limit") || 12), before = Number(query.get("cursor") || Infinity);
  const all = runs.map((run, index) => ({ ...run, session_id: id, created: index + 1 }));
  const eligible = all.filter(run => run.created < before && (lane !== "attention" || !["COMPLETED", "STOPPED", "FAILED"].includes(run.status)));
  const page = eligible.slice(-limit);
  const remaining = Math.max(0, eligible.length - limit);
  return { id, title, lane, runs: page, next_cursor: remaining ? String(page[0].created) : null, remaining,
    has_active: all.some(run => ["RUNNING", "QUEUED", "CONFIRMING", "STOPPING"].includes(run.status)) };
}
