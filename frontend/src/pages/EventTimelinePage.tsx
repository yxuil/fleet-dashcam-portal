/**
 * Event timeline page — `/trucks/:id/events` and `/drivers/:id/events`.
 *
 * One component, two scopes: the parent route picks which dimension to
 * scope to (`scope="truck" | "driver"`), and the page passes the id
 * through to `useTimelineEvents` as either `truckId` or `driverId`.
 *
 * Composition:
 *   - Header: shows the truck label or driver name, sourced from
 *     `useTrucks`/`useDrivers` (small per-tenant lists already cached
 *     by the search page).
 *   - Severity tabs: critical / high / medium / low / all. Local state
 *     because filter state is mostly UI-ephemeral here — unlike the
 *     search page, the timeline isn't a deep-linking target.
 *   - Type chips: multi-select toggles for the six event types.
 *   - List: `useTimelineEvents` infinite query → flatten → render
 *     `<EventRow />` per row. "Load more" button when more pages.
 *
 * Triage flow:
 *   - false_positive / coaching_note → `POST /events/:id/triage`,
 *     optimistically mark the row in local state. On error, revert
 *     and surface a toast-style banner.
 *   - open_case → open the modal. On submit, `useCreateCase` creates
 *     the case AND attaches `event.clip_id` in one mutation, then we
 *     also write the `open_case` triage audit (separately, so a failed
 *     audit doesn't undo the case), then navigate to `/cases/:newId`.
 *
 * Why the triage audit is a separate write
 * ---------------------------------------
 * The case-creation + clip-attachment are in one mutation. The triage
 * audit on the original event is a third call. We do it AFTER the case
 * is created so a failure mid-way doesn't leave a "case opened" audit
 * row for a case that doesn't exist. The audit is fire-and-forget: if
 * it fails we still navigate (the case exists), but log to the console.
 */

import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { EventRow } from "@/components/timeline/EventRow";
import {
  OpenCaseModal,
  type OpenCaseFormValues,
} from "@/components/timeline/OpenCaseModal";
import { Button } from "@/components/ui/Button";
import { useCreateCase } from "@/hooks/useCreateCase";
import { useDrivers } from "@/hooks/useDrivers";
import { useTimelineEvents } from "@/hooks/useTimelineEvents";
import { useTrucks } from "@/hooks/useTrucks";
import { apiPost, ApiError } from "@/lib/api";
import {
  EVENT_SEVERITIES,
  EVENT_TYPES,
  type EventRow as EventRowData,
  type EventSeverity,
  type EventType,
} from "@/lib/types";
import { cn } from "@/lib/utils";
import type { TriageLabel } from "@/hooks/useTriage";

type Scope = "truck" | "driver";

export type EventTimelinePageProps = {
  scope: Scope;
};

// "all" is just a UI sentinel — it maps to an empty severities[] filter.
type SeverityTab = EventSeverity | "all";
const SEVERITY_TABS: readonly SeverityTab[] = ["all", ...EVENT_SEVERITIES];

export function EventTimelinePage({ scope }: EventTimelinePageProps) {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  // -----------------------------------------------------------------
  // Filter state — UI-only. Not in the URL because the timeline pages
  // are reached via a click from the truck/driver list and aren't a
  // deep-link sharing target the way Search is.
  // -----------------------------------------------------------------
  const [severityTab, setSeverityTab] = useState<SeverityTab>("all");
  const [selectedTypes, setSelectedTypes] = useState<readonly EventType[]>([]);

  const severities = useMemo<readonly EventSeverity[]>(
    () => (severityTab === "all" ? [] : [severityTab]),
    [severityTab],
  );

  const filters = useMemo(
    () => ({
      truckId: scope === "truck" ? id : undefined,
      driverId: scope === "driver" ? id : undefined,
      severities,
      types: selectedTypes,
    }),
    [scope, id, severities, selectedTypes],
  );

  const events = useTimelineEvents(filters);

  // -----------------------------------------------------------------
  // Header data — truck label or driver name.
  // -----------------------------------------------------------------
  const trucks = useTrucks();
  const drivers = useDrivers();

  const headerLabel = useMemo(() => {
    if (!id) return "";
    if (scope === "truck") {
      return trucks.data?.find((t) => t.id === id)?.label ?? "Truck";
    }
    return drivers.data?.find((d) => d.id === id)?.name ?? "Driver";
  }, [scope, id, trucks.data, drivers.data]);

  // -----------------------------------------------------------------
  // Per-row triage state — maps event id → applied label.
  // We do NOT keep this in React Query: the audit endpoint never
  // mutates the event row, so there's nothing to invalidate. We just
  // need a local note of what the user picked.
  // -----------------------------------------------------------------
  const [triageByEventId, setTriageByEventId] = useState<
    Record<string, TriageLabel>
  >({});
  const [triageError, setTriageError] = useState<string | null>(null);

  const handleTriage = useCallback(
    async (event: EventRowData, label: TriageLabel) => {
      if (label === "open_case") {
        // Defer to the modal flow — handled separately below.
        setPendingOpenCaseEvent(event);
        return;
      }
      // Optimistic: mark immediately, roll back on error.
      const previous = triageByEventId[event.id] ?? null;
      setTriageByEventId((m) => ({ ...m, [event.id]: label }));
      setTriageError(null);
      try {
        await apiPost(`/events/${event.id}/triage`, { label });
      } catch (err) {
        // Revert.
        setTriageByEventId((m) => {
          const next = { ...m };
          if (previous === null) delete next[event.id];
          else next[event.id] = previous;
          return next;
        });
        const message =
          err instanceof ApiError
            ? `Triage failed (${err.status}): ${err.detail}`
            : "Triage failed.";
        setTriageError(message);
      }
    },
    [triageByEventId],
  );

  // -----------------------------------------------------------------
  // Open-case modal state.
  // -----------------------------------------------------------------
  const [pendingOpenCaseEvent, setPendingOpenCaseEvent] =
    useState<EventRowData | null>(null);
  const createCase = useCreateCase();

  const submitOpenCase = useCallback(
    async (values: OpenCaseFormValues) => {
      const ev = pendingOpenCaseEvent;
      if (!ev || !ev.clip_id) return;

      const incidentIso = toIsoOrNull(values.incidentAt) ?? ev.occurred_at;

      try {
        const created = await createCase.mutateAsync({
          case: {
            external_ref: values.externalRef.trim() || null,
            requester_name: values.requesterName.trim() || null,
            requester_org: values.requesterOrg.trim() || null,
            incident_at: incidentIso,
          },
          clipId: ev.clip_id,
          attachNote: `From event: ${ev.type} at ${ev.occurred_at}`,
        });

        // Best-effort audit; failure doesn't undo the case.
        apiPost(`/events/${ev.id}/triage`, {
          label: "open_case" as TriageLabel,
          note: `case ${created.number}`,
        }).catch((err) => {
          // eslint-disable-next-line no-console
          console.warn("[timeline] open_case audit failed", err);
        });

        setTriageByEventId((m) => ({ ...m, [ev.id]: "open_case" }));
        setPendingOpenCaseEvent(null);
        navigate(`/cases/${created.id}`);
      } catch {
        // Error is exposed via `createCase.error`; the modal stays open.
      }
    },
    [pendingOpenCaseEvent, createCase, navigate],
  );

  const closeOpenCaseModal = useCallback(() => {
    if (createCase.isPending) return;
    setPendingOpenCaseEvent(null);
    createCase.reset();
  }, [createCase]);

  // -----------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------
  if (!id) {
    return <p className="text-sm text-muted-foreground">Missing id.</p>;
  }

  const items = events.data?.items ?? [];
  const lastPage = events.data?.pages?.[events.data.pages.length - 1];
  const hasNextPage = Boolean(lastPage?.next_cursor);

  return (
    <section className="space-y-6">
      <Header scope={scope} label={headerLabel} />

      <SeverityTabs value={severityTab} onChange={setSeverityTab} />
      <TypeChips
        selected={selectedTypes}
        onToggle={(type) =>
          setSelectedTypes((current) =>
            current.includes(type)
              ? current.filter((t) => t !== type)
              : [...current, type],
          )
        }
        onClear={() => setSelectedTypes([])}
      />

      {triageError !== null ? (
        <p
          role="alert"
          data-testid="triage-error"
          className="rounded-md border border-destructive/50 bg-destructive/5 p-2 text-xs text-destructive"
        >
          {triageError}
        </p>
      ) : null}

      <Results
        isLoading={events.isLoading}
        isError={events.isError}
        error={events.error}
        items={items}
        triageByEventId={triageByEventId}
        onOpenClip={(clipId) => navigate(`/clips/${clipId}`)}
        onTriage={handleTriage}
        onRetry={() => events.refetch()}
      />

      {hasNextPage ? (
        <div className="flex justify-center pt-2">
          <Button
            variant="outline"
            size="sm"
            disabled={events.isFetchingNextPage}
            onClick={() => events.fetchNextPage()}
            data-testid="timeline-load-more"
          >
            {events.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}

      <OpenCaseModal
        open={pendingOpenCaseEvent !== null}
        defaultIncidentAt={pendingOpenCaseEvent?.occurred_at ?? ""}
        isSubmitting={createCase.isPending}
        error={createCase.error}
        onSubmit={submitOpenCase}
        onClose={closeOpenCaseModal}
      />
    </section>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

type HeaderProps = { scope: Scope; label: string };

function Header({ scope, label }: HeaderProps) {
  const subtitle = scope === "truck" ? "Truck events" : "Driver events";
  return (
    <header className="space-y-1">
      <Link
        to="/search"
        className="text-xs text-muted-foreground underline-offset-2 hover:underline"
      >
        ← Back
      </Link>
      <div className="flex items-baseline justify-between">
        <h1
          className="text-2xl font-semibold tracking-tight"
          data-testid="timeline-header-label"
        >
          {label}
        </h1>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Severity tabs
// ---------------------------------------------------------------------------

type SeverityTabsProps = {
  value: SeverityTab;
  onChange: (next: SeverityTab) => void;
};

function SeverityTabs({ value, onChange }: SeverityTabsProps) {
  return (
    <div
      role="tablist"
      aria-label="Severity"
      data-testid="severity-tabs"
      className="flex flex-wrap items-center gap-2"
    >
      {SEVERITY_TABS.map((tab) => {
        const selected = tab === value;
        return (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab)}
            data-testid={`severity-tab-${tab}`}
            className={cn(
              "h-8 rounded-md border px-3 text-xs font-medium capitalize transition-colors",
              selected
                ? "border-primary bg-primary text-primary-foreground"
                : "border-input bg-background hover:bg-accent",
            )}
          >
            {tab}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Type chips
// ---------------------------------------------------------------------------

type TypeChipsProps = {
  selected: readonly EventType[];
  onToggle: (type: EventType) => void;
  onClear: () => void;
};

function TypeChips({ selected, onToggle, onClear }: TypeChipsProps) {
  return (
    <div
      data-testid="type-chips"
      className="flex flex-wrap items-center gap-2"
    >
      <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        Type
      </span>
      {EVENT_TYPES.map((type) => {
        const on = selected.includes(type);
        return (
          <button
            key={type}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle(type)}
            data-testid={`type-chip-${type}`}
            className={cn(
              "h-7 rounded-full border px-3 text-[11px] font-medium transition-colors",
              on
                ? "border-primary bg-primary text-primary-foreground"
                : "border-input bg-background hover:bg-accent",
            )}
          >
            {type.replace(/_/g, " ")}
          </button>
        );
      })}
      {selected.length > 0 ? (
        <button
          type="button"
          onClick={onClear}
          className="text-[11px] text-muted-foreground underline-offset-2 hover:underline"
          data-testid="type-chips-clear"
        >
          clear
        </button>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

type ResultsProps = {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  items: EventRowData[];
  triageByEventId: Record<string, TriageLabel>;
  onOpenClip: (clipId: string) => void;
  onTriage: (event: EventRowData, label: TriageLabel) => void;
  onRetry: () => void;
};

function Results({
  isLoading,
  isError,
  error,
  items,
  triageByEventId,
  onOpenClip,
  onTriage,
  onRetry,
}: ResultsProps) {
  if (isLoading) {
    return (
      <div
        className="space-y-2"
        data-testid="timeline-loading"
        aria-busy="true"
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="h-12 animate-pulse rounded-md border border-border bg-card"
          />
        ))}
      </div>
    );
  }
  if (isError) {
    const status = error instanceof ApiError ? ` (${error.status})` : "";
    return (
      <div
        role="alert"
        data-testid="timeline-error"
        className="flex flex-col items-start gap-2 rounded-md border border-destructive/50 bg-destructive/5 p-4 text-sm"
      >
        <p className="font-medium text-destructive">
          Couldn’t load events{status}.
        </p>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div
        data-testid="timeline-empty"
        className="rounded-md border border-dashed border-border p-8 text-center text-sm text-muted-foreground"
      >
        No events match these filters.
      </div>
    );
  }
  return (
    <ul data-testid="timeline-list" className="space-y-2">
      {items.map((event) => (
        <EventRow
          key={event.id}
          event={event}
          triagedAs={triageByEventId[event.id] ?? null}
          onOpenClip={onOpenClip}
          onTriage={(label) => onTriage(event, label)}
        />
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Convert the `datetime-local` form value back into a server-side ISO
 * string. Treats the input as local time (which is what the picker
 * presents) and lets `Date.toISOString` normalise to UTC.
 *
 * Returns null if the input is empty/unparseable so the caller can fall
 * back to the event's `occurred_at`.
 */
function toIsoOrNull(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}
