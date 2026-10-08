/** Host-owned transport only: reconnect GET progress, never replay business mutations. */
export type RunEvent = { id: number; kind: string; data: Record<string, unknown> };
export type AgentRun = { id: string; input: string; provider: string; status: string; events: RunEvent[] };
export type Connection = "connecting" | "live" | "reconnecting" | "paused" | "idle";
export const isActive = (run: { status: string }) =>
  ["QUEUED", "RUNNING", "CONFIRMING", "STOPPING"].includes(run.status);
const RUN_STATUSES = new Set(["QUEUED", "RUNNING", "CONFIRMING", "STOPPING", "WAITING_CONFIRMATION", "COMPLETED", "FAILED", "STOPPED", "UNCERTAIN", "INTERRUPTED"]);
export class AgentHttpError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(status === 401 ? "登录已失效，请重新登录。" : status === 403 ? "无权访问此会话，请检查当前账号。" :
      status === 409 ? "操作状态已变化，请先核实结果。" : "请求未完成，请核实当前状态后再操作。");
    this.status = status;
  }
}
type Options = { signal?: AbortSignal; fetcher?: typeof fetch; onUnauthorized?: () => void };
export async function requestAgent<T>(path: string, authorization: string, body?: unknown, method?: string, options: Options = {}): Promise<T> {
  const res = await (options.fetcher || fetch)("/api/agent" + path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: { Authorization: authorization, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: options.signal || AbortSignal.timeout(60000), cache: "no-store", redirect: "error",
  });
  // Authentication is checked BEFORE JSON: gateways can return an HTML 401 page.
  if (res.status === 401) options.onUnauthorized?.();
  if (!res.ok) throw new AgentHttpError(res.status);
  try { return await res.json() as T; }
  catch { throw new Error("服务返回的状态无法读取，请刷新核实。"); }
}
function eventValue(value: unknown): RunEvent {
  const e = value as RunEvent;
  if (!e || !Number.isSafeInteger(e.id) || e.id <= 0 || typeof e.kind !== "string" ||
      !e.data || typeof e.data !== "object" || Array.isArray(e.data) ||
      (e.kind === "state" && !RUN_STATUSES.has(String(e.data.status))))
    throw new Error("进度数据格式异常，请重新读取会话。");
  return e;
}
export function runValue(value: unknown, expectedId: string): AgentRun {
  const r = value as AgentRun;
  if (!r || r.id !== expectedId || !RUN_STATUSES.has(r.status) || typeof r.input !== "string" ||
      typeof r.provider !== "string" || !Array.isArray(r.events))
    throw new Error("返回的会话状态不匹配，请重新读取。");
  r.events.forEach(eventValue);
  return r;
}
/** Incremental UTF-8 SSE parser: LF/CRLF/CR, comments, multiline data, replay IDs and bounded frames. */
export class EventDecoder {
  private decoder = new TextDecoder();
  private pending = "";
  private data: string[] = [];
  private size = 0;
  cursor: number;
  constructor(cursor = 0) { this.cursor = cursor; }
  push(chunk: Uint8Array): RunEvent[] {
    this.pending += this.decoder.decode(chunk, { stream: true });
    const result: RunEvent[] = [];
    for (;;) {
      const end = this.pending.search(/[\r\n]/);
      if (end < 0 || (this.pending[end] === "\r" && end + 1 === this.pending.length)) break;
      const line = this.pending.slice(0, end);
      const width = this.pending[end] === "\r" && this.pending[end + 1] === "\n" ? 2 : 1;
      this.pending = this.pending.slice(end + width);
      this.size += line.length + width;
      if (this.size > 262144) throw new Error("进度帧过大，请重新读取会话。");
      if (line === "") {
        if (this.data.length) {
          let parsed: unknown;
          try { parsed = JSON.parse(this.data.join("\n")); }
          catch { throw new Error("进度数据格式异常，请重新读取会话。"); }
          const event = eventValue(parsed);
          if (event.id > this.cursor) { this.cursor = event.id; result.push(event); }
        }
        this.data = []; this.size = 0;
      } else if (line === "data" || line.startsWith("data:")) {
        this.data.push(line.slice(5).replace(/^ /, ""));
      }
    }
    if (this.size + this.pending.length > 262144) throw new Error("进度帧过大，请重新读取会话。");
    return result;
  }
  // Do not dispatch an unterminated frame at EOF; persisted GET state remains authoritative.
}
export function applyEvent(run: AgentRun, event: RunEvent): AgentRun {
  if (run.events.some(e => e.id === event.id)) return run;
  const events = [...run.events, event].sort((a, b) => a.id - b.id);
  return { ...run, events, status: event.kind === "state" && typeof event.data.status === "string" ? event.data.status : run.status };
}
export function createLatch() {
  let occupied = false;
  return { enter() { if (occupied) return false; occupied = true; return true; }, leave() { occupied = false; } };
}
function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
    const onAbort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", onAbort); resolve(); }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
type FollowOptions = Options & {
  signal: AbortSignal; cursor?: number; onEvent: (event: RunEvent) => void;
  onSnapshot: (run: AgentRun) => void; onConnection: (state: Connection) => void;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>; idleMs?: number;
};
/** At most three subscriptions per invocation. Manual recovery starts a new bounded GET-only invocation. */
export async function followAgentRun(id: string, authorization: string, options: FollowOptions): Promise<void> {
  let cursor = options.cursor || 0;
  const notify = (state: Connection) => { if (!options.signal.aborted) options.onConnection(state); };
  const base = `/runs/${encodeURIComponent(id)}`;
  for (let attempt = 0; attempt < 3 && !options.signal.aborted; attempt++) {
    if (attempt) { notify("reconnecting"); try { await (options.sleep || pause)([0, 500, 1500][attempt], options.signal); } catch { return; } }
    else notify("connecting");
    const connection = new AbortController();
    const cancel = () => connection.abort();
    options.signal.addEventListener("abort", cancel, { once: true });
    let idle: ReturnType<typeof setTimeout> | undefined;
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    const resetIdle = () => { clearTimeout(idle); idle = setTimeout(cancel, options.idleMs ?? 25000); };
    const cancelReader = () => { void reader?.cancel().catch(() => {}); };
    try {
      resetIdle();
      const res = await (options.fetcher || fetch)(`/api/agent${base}/events?after=${cursor}`, {
        headers: { Authorization: authorization }, signal: connection.signal, cache: "no-store", redirect: "error",
      });
      if (res.status === 401) options.onUnauthorized?.();
      if (!res.ok) throw new AgentHttpError(res.status);
      if (!res.body || !res.headers.get("content-type")?.includes("text/event-stream")) throw new Error("Progress unavailable");
      if (options.signal.aborted) return;
      notify("live"); reader = res.body.getReader();
      connection.signal.addEventListener("abort", cancelReader, { once: true });
      const parser = new EventDecoder(cursor);
      while (!connection.signal.aborted) {
        const part = await reader.read();
        if (part.done || options.signal.aborted) break;
        resetIdle();
        for (const event of parser.push(part.value)) {
          if (options.signal.aborted) return;
          cursor = event.id; options.onEvent(event);
        }
      }
    } catch (error) {
      if (options.signal.aborted) return;
      if (error instanceof AgentHttpError && [401, 403, 404].includes(error.status)) { notify("paused"); return; }
    } finally {
      clearTimeout(idle); options.signal.removeEventListener("abort", cancel);
      connection.signal.removeEventListener("abort", cancelReader);
      if (reader) { try { await reader.cancel(); } catch { /* Already closed. */ } reader.releaseLock(); }
      connection.abort();
    }
    if (options.signal.aborted) return;
    // EOF/transport failure is NOT run completion. Read persisted state before deciding.
    try {
      const signal = AbortSignal.any([options.signal, AbortSignal.timeout(20000)]);
      const run = runValue(await requestAgent<unknown>(base, authorization, undefined, "GET", { ...options, signal }), id);
      if (options.signal.aborted) return;
      options.onSnapshot(run);
      cursor = run.events.reduce((value, event) => Math.max(value, event.id), cursor);
      if (!isActive(run)) { notify("idle"); return; }
    } catch (error) {
      if (options.signal.aborted) return;
      if (error instanceof AgentHttpError && [401, 403, 404].includes(error.status)) { notify("paused"); return; }
    }
  }
  notify("paused");
}
