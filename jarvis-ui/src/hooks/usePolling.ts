/**
 * B2 — polling helper hook.
 *
 * Calls `fn` every `ms` while `enabled` stays true. Keeps the latest
 * callback in a ref so the interval doesn't capture a stale closure
 * even when `fn` changes between renders.
 */
import { useEffect, useRef } from "react";

export function usePolling(fn: () => void, ms: number, enabled = true) {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (!enabled) return;
    const id = setInterval(() => fnRef.current(), ms);
    return () => clearInterval(id);
  }, [ms, enabled]);
}
