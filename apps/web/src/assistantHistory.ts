/** Rendering window only. Retains original data and always exposes actionable/unknown states. */
export const HISTORY_BATCH = 12;
const FOLDABLE = new Set(["COMPLETED", "STOPPED", "FAILED"]);
export function historyWindow<T extends { id: string; status: string }>(
  runs: readonly T[], recent: number, pendingId: string | null = null,
): { visible: T[]; hidden: number } {
  if (!Number.isSafeInteger(recent) || recent < 1) throw new TypeError("Invalid history window");
  const start = Math.max(0, runs.length - recent);
  const visible = runs.filter((run, index) => index >= start || run.id === pendingId || !FOLDABLE.has(run.status));
  return { visible, hidden: runs.length - visible.length };
}
