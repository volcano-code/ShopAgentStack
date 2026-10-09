/** Shared optional resource, separate from Suspense so readable content never suspends. */
export type OptionalSnapshot<T> = { state: "idle" | "pending" | "failed" } | { state: "ready"; value: T };
export function createOptionalModule<T>(load: () => Promise<T>) {
  let snapshot: OptionalSnapshot<T> = { state: "idle" };
  let pending: Promise<void> | null = null;
  const listeners = new Set<() => void>();
  const publish = () => { for (const listener of listeners) listener(); };
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    start: () => {
      if (pending) return pending;
      pending = Promise.resolve().then(load).then(
        value => { snapshot = { state: "ready", value }; publish(); },
        () => { snapshot = { state: "failed" }; publish(); },
      );
      snapshot = { state: "pending" }; publish();
      return pending;
    },
  };
}
