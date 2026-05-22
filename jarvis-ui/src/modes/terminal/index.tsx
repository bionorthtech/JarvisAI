/**
 * B2 — Terminal mode.
 *
 * Interactive bash shell over WebSocket PTY. Backend handler lives in
 * `main.py` (`/ws/terminal`). The xterm.js terminal binds binary frames
 * both directions; ResizeObserver keeps the PTY cols/rows in sync with
 * the visible area.
 */
import { useEffect, useRef } from "react";
import { Terminal } from "lucide-react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import { useTheme } from "../../hooks/useTheme";

interface Props {
  wsUrl: string;
}

export default function TerminalPane({ wsUrl }: Props) {
  const { t, theme } = useTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<XTerm | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitRef = useRef<FitAddon | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const cursor = theme === "apple" ? "#0a84ff" : "#f59e0b";
    const selection = theme === "apple"
      ? "rgba(10,132,255,0.25)"
      : "rgba(251,191,36,0.2)";
    const term = new XTerm({
      theme: {
        background: "#060607",
        foreground: "#d4d4d4",
        cursor,
        selectionBackground: selection,
      },
      fontFamily: '"JetBrains Mono", "Cascadia Code", monospace',
      fontSize: 13,
      lineHeight: 1.4,
      cursorBlink: true,
      scrollback: 5000,
      allowProposedApi: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    requestAnimationFrame(() => fit.fit());
    termRef.current = term;
    fitRef.current = fit;

    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
      term.focus();
    };
    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) term.write(new Uint8Array(e.data));
      else term.write(e.data as string);
    };
    ws.onclose = () => term.write("\r\n\x1b[33m[connection closed]\x1b[0m\r\n");
    ws.onerror = () => term.write("\r\n\x1b[31m[WebSocket error]\x1b[0m\r\n");
    term.onData(data => { if (ws.readyState === WebSocket.OPEN) ws.send(data); });

    const ro = new ResizeObserver(() => {
      requestAnimationFrame(() => {
        try {
          fit.fit();
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
          }
        } catch { /* ignore */ }
      });
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      ws.close();
      term.dispose();
    };
  }, [wsUrl, theme]);

  return (
    <div className="flex-1 flex flex-col bg-[var(--surface-2)] overflow-hidden">
      <div className="shrink-0 flex items-center gap-2 px-4 py-2 border-b border-white/[0.04]">
        <Terminal size={13} className={t.accent} />
        <span className="text-[11px] text-[var(--text-muted)]">Interactive Shell</span>
        <span className="ml-auto text-[9px] text-[var(--text-subtle)]">bash · pty</span>
      </div>
      <div ref={containerRef} className="flex-1 p-3 overflow-hidden" style={{ minHeight: 0 }} />
    </div>
  );
}
