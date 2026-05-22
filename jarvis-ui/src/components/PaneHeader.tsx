/**
 * B2 — shared pane header.
 *
 * 2026-05-18 retarget: large sentence-case title (was all-caps
 * tracked-out at 14px). Icon sits next to the title in muted color;
 * lastRefreshed sits below as a small subtitle. Children slot keeps
 * its right-aligned action area.
 */
import type { ReactNode } from "react";
import { fmtAgo } from "../utils/format";

interface Props {
  icon: ReactNode;
  title: string;
  lastRefreshed?: Date | null;
  children?: ReactNode;
}

export default function PaneHeader({ icon, title, lastRefreshed, children }: Props) {
  return (
    <div className="flex items-center gap-3 mb-4">
      <span className="text-[var(--text-muted)]">{icon}</span>
      <div className="flex flex-col">
        <h2 className="text-2xl font-semibold text-[var(--text)] leading-tight">{title}</h2>
        {lastRefreshed !== undefined && (
          <span className="text-[11px] text-[var(--text-subtle)] tabular-nums">{fmtAgo(lastRefreshed)}</span>
        )}
      </div>
      <div className="ml-auto flex items-center gap-2">{children}</div>
    </div>
  );
}
