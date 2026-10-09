/** A bounded module load, not a business-request retry mechanism. */
export class PageModuleError extends Error {
  constructor() {
    super("PAGE_MODULE_UNAVAILABLE");
    this.name = "PageModuleError";
  }
}

export function loadPageModule<T>(load: () => Promise<T>, timeoutMs = 15000): Promise<T> {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new TypeError("Invalid load timeout");
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      settled = true;
      reject(new PageModuleError());
    }, timeoutMs);
    Promise.resolve().then(load).then(value => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    }, () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      // Do not expose asset URLs, proxy response bodies, or internal error details.
      reject(new PageModuleError());
    });
  });
}
