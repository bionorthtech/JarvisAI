/**
 * B2 — live WebSocket subscriber hook.
 *
 * Opens a WS connection to `url`, auto-reconnects with a 3s backoff,
 * and surfaces the last 200 non-ping bus events. Used by the Dashboard
 * (and any future pane that wants a live feed of `bus.py` traffic).
 */
import { useEffect, useRef, useState } from "react";

export interface BusEvent {
  id?: string;
  ts?: number;
  topic: string;
  sender?: string;
  [k: string]: unknown;
}

export function useLiveWS(url: string) {
  const [events, setEvents] = useState<BusEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let ws: WebSocket;
    let retryTimer: ReturnType<typeof setTimeout>;
    let dead = false;

    function connect() {
      if (dead) return;
      ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!dead) retryTimer = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        try {
          const msg: BusEvent = JSON.parse(e.data);
          if (msg.topic === "ping") return;
          setEvents((prev) => [msg, ...prev].slice(0, 200));
        } catch {
          /* ignore malformed */
        }
      };
    }

    connect();
    return () => {
      dead = true;
      clearTimeout(retryTimer);
      ws?.close();
    };
  }, [url]);

  return { events, connected };
}
