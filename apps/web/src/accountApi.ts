import { token } from "./api";

export async function accountApi<T>(
  path: string,
  body?: unknown,
  method?: string,
): Promise<T> {
  const res = await fetch("/api/agent" + path, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: {
      Authorization: token("portal"),
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(50000),
  });
  if (res.status === 401) {
    window.dispatchEvent(new Event("shop_agent_stack-session-expired"));
    throw new Error("登录已过期");
  }
  const data = await res.json();
  if (!res.ok)
    throw new Error(
      typeof data.detail === "string" ? data.detail : "请求未完成",
    );
  return data;
}
