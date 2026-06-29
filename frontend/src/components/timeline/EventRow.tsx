/**
 * One row in the event timeline.
 *
 * Pure presentational — all data fetching / mutation lives one level
 * up in `EventTimelinePage`. The row receives:
 *   - `event`: the `EventRow` API shape.
 *   - `onOpenClip`: invoked when the user clicks "open clip". The
 *     parent navigates so this component stays test-friendly.
 *   - `onTriage`: invoked with one of three labels when the user picks
 *     a triage option. "open_case" opens the modal at the page level.
 *   - `triagedAs`: the last successfully-applied triage label, surfaced
 *     as an inline badge so the user knows the row was acted on.
 *
 * Events with `clip_id === null` disable both "Open clip" and "Open case"
 * — the case attachment step needs a clip to attach.
 */

import { Button } from "@/components/ui/Button";
import {
  Dropdown,
  DropdownItem,
  DropdownSeparator,
} from "@/components/ui/Dropdown";
import type { EventRow as EventRowData, EventSeverity } from "@/lib/types";
import { cn } from "@/lib/utils";

import type { TriageLabel } from "@/hooks/useTriage";

import { formatTelemetrySnippet } from "./telemetry";

const SEVERITY_BADGE: Record<EventSeverity, string> = {
  critical: "bg-red-500 text-white",
  high: "bg-orange-500 text-white",
  medium: "bg-yellow-400 text-yellow-950",
  low: "bg-slate-300 text-slate-800",
};

const TRIAGE_BADGE: Record<TriageLabel, string> = {
  false_positive: "bg-slate-200 text-slate-700",
  coaching_note: "bg-blue-100 text-blue-800",
  open_case: "bg-emerald-100 text-emerald-800",
};

const TRIAGE_LABEL_TEXT: Record<TriageLabel, string> = {
  false_positive: "False positive",
  coaching_note: "Coaching note",
  open_case: "Case opened",
};

export type EventRowProps = {
  event: EventRowData;
  triagedAs: TriageLabel | null;
  onOpenClip: (clipId: string) => void;
  onTriage: (label: TriageLabel) => void;
};

export function EventRow({
  event,
  triagedAs,
  onOpenClip,
  onTriage,
}: EventRowProps) {
  const hasClip = event.clip_id !== null;
  const snippet = formatTelemetrySnippet(event.telemetry);
  const ts = formatTimestamp(event.occurred_at);

  return (
    <li
      data-testid={`event-row-${event.id}`}
      className="flex items-center justify-between gap-4 rounded-md border border-border bg-card px-4 py-3"
    >
      <div className="flex flex-1 items-center gap-4">
        <time
          dateTime={event.occurred_at}
          className="w-32 shrink-0 text-xs tabular-nums text-muted-foreground"
          data-testid={`event-ts-${event.id}`}
        >
          {ts}
        </time>

        <span
          className="w-36 shrink-0 text-sm font-medium"
          data-testid={`event-type-${event.id}`}
        >
          {formatEventType(event.type)}
        </span>

        <span
          className={cn(
            "inline-flex h-5 shrink-0 items-center rounded-full px-2 text-[10px] font-semibold uppercase tracking-wide",
            SEVERITY_BADGE[event.severity],
          )}
          data-testid={`event-severity-${event.id}`}
        >
          {event.severity}
        </span>

        <span
          className="flex-1 truncate text-xs text-muted-foreground"
          data-testid={`event-telemetry-${event.id}`}
        >
          {snippet}
        </span>

        {triagedAs !== null ? (
          <span
            className={cn(
              "inline-flex h-5 shrink-0 items-center rounded-full px-2 text-[10px] font-medium",
              TRIAGE_BADGE[triagedAs],
            )}
            data-testid={`event-triaged-${event.id}`}
          >
            {TRIAGE_LABEL_TEXT[triagedAs]}
          </span>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={!hasClip}
          title={hasClip ? undefined : "No clip attached"}
          onClick={() => {
            if (hasClip && event.clip_id) onOpenClip(event.clip_id);
          }}
          data-testid={`event-open-clip-${event.id}`}
        >
          Open clip
        </Button>

        <Dropdown
          trigger={({ open }) => (
            <button
              type="button"
              className={cn(
                "inline-flex h-8 items-center rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-accent",
                open && "bg-accent",
              )}
              data-testid={`event-triage-trigger-${event.id}`}
            >
              Triage ▾
            </button>
          )}
        >
          <DropdownItem
            onSelect={() => onTriage("false_positive")}
            data-testid={`event-triage-fp-${event.id}`}
          >
            False positive
          </DropdownItem>
          <DropdownItem
            onSelect={() => onTriage("coaching_note")}
            data-testid={`event-triage-coach-${event.id}`}
          >
            Coaching note
          </DropdownItem>
          <DropdownSeparator />
          <DropdownItem
            onSelect={() => onTriage("open_case")}
            disabled={!hasClip}
            data-testid={`event-triage-open-case-${event.id}`}
            className={!hasClip ? "opacity-50" : undefined}
          >
            Open case
          </DropdownItem>
        </Dropdown>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatTimestamp(iso: string): string {
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

function formatEventType(t: EventRowData["type"]): string {
  const lower = t.replace(/_/g, " ");
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}
