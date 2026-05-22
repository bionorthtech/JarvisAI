/**
 * B2 — D2.3 Mood Ribbon.
 *
 * Persistent footer mounted at App root — shows the most-recent
 * thought-broadcast regardless of which mode is active. Quiet by
 * design: dim italic, fixed bottom-right, slides on new thoughts,
 * hides on click of the × control. Polls /thoughts/recent?limit=1
 * every 8s.
 */
import { useEffect, useState } from "react";
import { BACKEND } from "../config";

export default function MoodRibbon() {
  type Thought = { ts?: number; thought?: string; sender?: string };
  const [latest, setLatest] = useState<Thought | null>(null);
  const [dismissed, setDismissed] = useState<number | null>(null);
  const [pulse, setPulse] = useState(false);

  useEffect(() => {
    const poll = () => {
      fetch(`${BACKEND}/thoughts/recent?limit=1`).then(r => r.json())
        .then(d => {
          const t = (d.thoughts ?? [])[0];
          if (!t) return;
          setLatest(prev => {
            if (prev?.ts === t.ts) return prev;
            setPulse(true);
            window.setTimeout(() => setPulse(false), 1500);
            return t;
          });
        })
        .catch(() => {});
    };
    poll();
    const id = window.setInterval(poll, 8000);
    return () => window.clearInterval(id);
  }, []);

  if (!latest?.thought) return null;
  if (dismissed && latest.ts === dismissed) return null;

  return (
    <div
      className={`fixed bottom-2 right-2 max-w-md z-30 bg-[var(--surface-1)]/95 backdrop-blur rounded-xl px-4 py-2 text-[12px] text-[var(--text-muted)] italic flex items-center gap-2 apple-card-shadow transition-all duration-300 ${pulse ? "scale-105 ring-1 ring-amber-500/30" : ""}`}
    >
      <span className="text-[var(--text-subtle)] not-italic shrink-0">
        {latest.sender ? `[${latest.sender}]` : "·"}
      </span>
      <span className="line-clamp-2 flex-1">{latest.thought}</span>
      <button onClick={() => setDismissed(latest.ts ?? null)}
        className="text-[var(--text-subtle)] hover:text-[var(--text)] shrink-0">×</button>
    </div>
  );
}
