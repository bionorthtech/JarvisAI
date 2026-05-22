/**
 * 3B — Apps & Plugins mode, rebuilt against Card + Section primitives.
 *
 * Three sections:
 * 1. Internet status — read-only mirror of SecurityConfig
 *    `internet_access`. The real switch is `~/jarvis/config/jarvis.toml`
 *    (not flippable from the UI by design — see plugins/web_search and
 *    agent/core/adapters for the offline-guard contract).
 * 2. Plugin list with per-plugin enable/disable toggles.
 * 3. App permissions matrix (allow/ask/block).
 *
 * Polls every 30s.
 */
import { useCallback, useEffect, useState } from "react";
import { AppWindow, Globe, Layers, ToggleLeft, ToggleRight, Lock } from "lucide-react";

import { BACKEND } from "../../config";
import { usePolling } from "../../hooks/usePolling";
import PaneHeader from "../../components/PaneHeader";
import { Card, Section } from "../../components/widgets";

const PERM_COLORS: Record<string, string> = {
  allow: "text-emerald-400 border-emerald-500/30 bg-emerald-500/10",
  ask:   "text-amber-400 border-amber-500/30 bg-amber-500/10",
  block: "text-red-400 border-red-500/30 bg-red-500/10",
};

export default function AppsPane() {
  const [permissions, setPermissions] = useState<Record<string, string>>({});
  const [plugins, setPlugins] = useState<{ name: string; description: string }[]>([]);
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [offlineMode, setOfflineMode] = useState<boolean>(true);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);

  const load = useCallback(() => {
    fetch(`${BACKEND}/apps/permissions`)
      .then(r => r.json()).then(setPermissions).catch(() => {});
    fetch(`${BACKEND}/plugins`)
      .then(r => r.json()).then(d => setPlugins(d.plugins ?? [])).catch(() => {});
    fetch(`${BACKEND}/plugins/overrides`)
      .then(r => r.json())
      .then(d => { setOverrides(d); setLastRefreshed(new Date()); })
      .catch(() => {});
    // 3B/3A — `offline_mode` want returns satisfied when SecurityConfig
    // .internet_access is False (the desired state). Mirror it here as
    // a read-only status indicator instead of the speculative toggle
    // that used to live in this pane.
    fetch(`${BACKEND}/wants`)
      .then(r => r.json())
      .then(d => {
        const w = (d.wants || []).find((w: { id: string }) => w.id === "offline_mode");
        if (w) setOfflineMode(w.status === "satisfied");
      })
      .catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);
  usePolling(load, 30_000);

  const setAppPerm = (app: string, perm: string) => {
    fetch(`${BACKEND}/apps/permissions/${encodeURIComponent(app)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permission: perm }),
    }).then(() => setPermissions(p => ({ ...p, [app]: perm })));
  };

  const togglePlugin = (name: string) => {
    fetch(`${BACKEND}/plugins/${encodeURIComponent(name)}/toggle`, { method: "PATCH" })
      .then(r => r.json())
      .then(d => setOverrides(p => ({ ...p, [name]: d.enabled })));
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto p-6 space-y-6">
        <PaneHeader icon={<AppWindow size={13} />} title="Apps & plugins" lastRefreshed={lastRefreshed} />

        <Section title="Network">
          <Card title="Internet Status">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className={offlineMode ? "text-emerald-400" : "text-red-400"}>
                  {offlineMode ? <Lock size={16} /> : <Globe size={16} />}
                </span>
                <div>
                  <p className="text-[12px] text-[var(--text)]">
                    {offlineMode
                      ? "Fully offline"
                      : "Internet access enabled"}
                  </p>
                  <p className="text-[10px] text-[var(--text-subtle)] mt-0.5">
                    {offlineMode
                      ? "JARVIS will not make outbound calls beyond 127.0.0.1. To enable, set security.internet_access = true in ~/jarvis/config/jarvis.toml."
                      : "web_search + webhook adapters can call external hosts. Disable in jarvis.toml to lock down."}
                  </p>
                </div>
              </div>
            </div>
          </Card>
        </Section>

        <Section title={`Plugins (${plugins.length})`}>
          <Card title="Loaded plugins" actions={<Layers size={11} className="text-[var(--text-subtle)]" />}>
            <div className="space-y-2">
              {plugins.map(p => {
                const enabled = overrides[p.name] !== false;
                return (
                  <div
                    key={p.name}
                    className="flex items-center justify-between py-2 border-b border-white/[0.03] last:border-0"
                  >
                    <div>
                      <span className="text-[12px] font-mono text-[var(--text)]">{p.name}</span>
                      <p className="text-[10px] text-[var(--text-subtle)] mt-0.5">{p.description?.slice(0, 60)}</p>
                    </div>
                    <button
                      onClick={() => togglePlugin(p.name)}
                      className={`flex items-center gap-1.5 px-3 py-1 rounded-lg border text-[10px] transition-all ${
                        enabled
                          ? "border-emerald-500/20 bg-emerald-500/5 text-emerald-400"
                          : "border-white/[0.04] text-[var(--text-subtle)]"
                      }`}
                    >
                      {enabled ? <ToggleRight size={12} /> : <ToggleLeft size={12} />}
                      {enabled ? "enabled" : "disabled"}
                    </button>
                  </div>
                );
              })}
              {plugins.length === 0 && (
                <p className="text-[11px] text-[var(--text-subtle)] py-3">No plugins discovered.</p>
              )}
            </div>
          </Card>
        </Section>

        {Object.keys(permissions).length > 0 && (
          <Section title="App permissions">
            <Card title="Per-app launch policy">
              <div className="space-y-2">
                {Object.entries(permissions).map(([app, perm]) => (
                  <div
                    key={app}
                    className="flex items-center justify-between py-1.5 border-b border-white/[0.03] last:border-0"
                  >
                    <span className="text-[11px] font-mono text-[var(--text-muted)]">{app}</span>
                    <div className="flex gap-1">
                      {["allow", "ask", "block"].map(p => (
                        <button
                          key={p}
                          onClick={() => setAppPerm(app, p)}
                          className={`px-2 py-0.5 text-[9px] rounded border transition-all ${
                            perm === p ? PERM_COLORS[p] : "border-white/[0.04] text-[var(--text-subtle)] hover:text-[var(--text-muted)]"
                          }`}
                        >
                          {p}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          </Section>
        )}
      </div>
    </div>
  );
}
