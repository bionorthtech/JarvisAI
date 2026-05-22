/**
 * B2 — Toast container.
 *
 * Mounted at the App root. Renders the in-app notification queue
 * (max-5 trailing) from `<App />`. Auto-dismiss happens in App;
 * this component is presentational only.
 */
import { BellRing, X } from "lucide-react";
import type { Toast } from "../types";

export default function ToastContainer({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: string) => void }) {
  if (toasts.length === 0) return null;
  return (
    <div className="fixed bottom-4 right-4 z-50 space-y-2 max-w-xs">
      {toasts.map(toast => (
        <div key={toast.id} className={`flex items-start gap-3 p-3 rounded-xl border shadow-2xl backdrop-blur-sm ${
          toast.type === "error" ? "bg-red-900/80 border-red-500/30 text-red-200" :
          toast.type === "warn"  ? "bg-amber-900/80 border-amber-500/30 text-amber-200" :
          "bg-slate-900/90 border-white/10 text-[var(--text)]"
        }`}>
          <BellRing size={13} className="shrink-0 mt-0.5" />
          <p className="text-[11px] flex-1">{toast.message}</p>
          <button onClick={() => dismiss(toast.id)} className="shrink-0 opacity-50 hover:opacity-100 transition-opacity">
            <X size={11} />
          </button>
        </div>
      ))}
    </div>
  );
}
