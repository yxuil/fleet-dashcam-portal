/**
 * Single clip card rendered on the Search results grid.
 *
 * Layout: a 16:9 thumbnail placeholder on top, metadata below.
 * Clicking the card navigates to `/clips/:id`. The whole tile is a
 * single `<button>` so keyboard activation Just Works (Enter / Space).
 */

import { useNavigate } from "react-router-dom";

import type { ClipRow } from "@/lib/types";

type Props = {
  clip: ClipRow;
};

/** Format an ISO timestamp as `12 Jun 14:32` in the local zone. */
function formatStartedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

/** Format `duration_s` as `1m 23s` or `45s`. */
function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m === 0) return `${s}s`;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

export function ClipCard({ clip }: Props) {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      onClick={() => navigate(`/clips/${clip.id}`)}
      data-testid={`clip-card-${clip.id}`}
      className="group flex flex-col overflow-hidden rounded-lg border border-border bg-card text-left shadow-sm transition-shadow hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div
        className="relative aspect-video w-full bg-slate-200"
        aria-hidden
        data-testid="clip-thumb"
      >
        {/* Placeholder thumbnail. Later: render first-frame canvas. */}
        <div className="absolute inset-0 flex items-center justify-center text-xs text-slate-500">
          {formatDuration(clip.duration_s)}
        </div>
      </div>

      <div className="space-y-1 p-3">
        <div className="flex items-center justify-between gap-2">
          <span
            className="truncate text-sm font-semibold"
            data-testid="clip-truck-label"
          >
            {clip.truck_label}
          </span>
          <span
            className="rounded-full bg-muted px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground"
            title="Event association not yet wired in T11."
          >
            —
          </span>
        </div>
        <div className="text-xs text-muted-foreground">
          <span data-testid="clip-driver-name">
            {clip.driver_name ?? "—"}
          </span>
          <span aria-hidden> · </span>
          <span data-testid="clip-started-at">
            {formatStartedAt(clip.started_at)}
          </span>
        </div>
      </div>
    </button>
  );
}
