/**
 * One day-card in a Fleet Cam truck row.
 *
 * Renders a thumbnail (placeholder gradient for now), the day label,
 * a clip count, and the total duration. Clicking the card navigates
 * to the day's first clip.
 */

import { useNavigate } from "react-router-dom";

import type { TruckDay } from "@/lib/types";
import { cn } from "@/lib/utils";

const DATE_FMT = new Intl.DateTimeFormat("en", {
  month: "short",
  day: "numeric",
});

function formatDate(isoDate: string): string {
  // The backend serialises `date` as `YYYY-MM-DD`. Parse the raw parts
  // so we don't introduce a timezone shift via `new Date("YYYY-MM-DD")`,
  // which JavaScript treats as UTC midnight and then renders in the
  // viewer's local zone — that can flip a Jun 28 clip into Jun 27 for
  // a westbound user.
  const [y, m, d] = isoDate.split("-").map((part) => Number(part));
  if (!y || !m || !d) return isoDate;
  return DATE_FMT.format(new Date(y, m - 1, d));
}

function formatDuration(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${seconds}s`;
}

export type DayCardProps = {
  day: TruckDay;
  className?: string;
};

export function DayCard({ day, className }: DayCardProps) {
  const navigate = useNavigate();
  const label = formatDate(day.date);
  const clipLabel = `${day.clip_count} clip${day.clip_count === 1 ? "" : "s"}`;
  const duration = formatDuration(day.total_duration_s);

  return (
    <button
      type="button"
      data-testid={`day-card-${day.date}`}
      onClick={() => navigate(`/clips/${day.first_clip_id}`)}
      className={cn(
        "flex w-40 shrink-0 flex-col overflow-hidden rounded-lg border border-border bg-card text-left transition-colors hover:border-primary",
        className,
      )}
    >
      {/* Placeholder thumbnail — gradient until real frame extraction lands. */}
      <div
        aria-hidden
        className="h-20 w-full bg-gradient-to-br from-slate-300 to-slate-500"
      />
      <div className="space-y-0.5 px-3 py-2">
        <div className="text-sm font-medium text-foreground">{label}</div>
        <div className="text-xs text-muted-foreground">{clipLabel}</div>
        <div className="text-xs text-muted-foreground">{duration}</div>
      </div>
    </button>
  );
}
