import { AlertTriangle } from "lucide-react";
import type { LMStatus } from "../types";

/**
 * Global banner shown when LM Studio is unreachable AND the backend
 * has classified the cause (firewall block / not running / ipv6-only).
 * Fed by `/health` -> `lm_studio.blocked_hint`. Mounts once at app
 * root, visible across every mode so the user always knows why JARVIS
 * is silent.
 *
 * No "dismiss" button — the condition is observable and either real
 * (banner stays) or fixed (banner disappears on next /health poll).
 */
export default function LMBlockedBanner({ lm }: { lm: LMStatus }) {
  if (lm.connected || !lm.blocked_hint) return null;
  return (
    <div className="bg-amber-950/40 border-y border-amber-700/40 px-4 py-2 flex items-center gap-2">
      <AlertTriangle size={14} className="text-amber-400 shrink-0" />
      <div className="text-[11px] text-amber-200">
        <span className="font-semibold">LM Studio unreachable.</span>{" "}
        <span className="text-amber-200/80">{lm.blocked_hint}</span>
      </div>
    </div>
  );
}
