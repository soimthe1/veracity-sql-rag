import { useEffect, useRef, useState } from "react";

/**
 * Polls `fn` every `intervalMs`, starting immediately. A failed call
 * surfaces its error but keeps the last-good data on screen and keeps
 * polling — matches the "graceful degradation" pattern the catalog API
 * itself follows (never throw the whole page away over one bad tick).
 */
export function usePolling<T>(fn: () => Promise<T>, intervalMs: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function tick() {
      try {
        const result = await fnRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) {
          setLoading(false);
          timer = setTimeout(tick, intervalMs);
        }
      }
    }

    setLoading(true);
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading };
}
