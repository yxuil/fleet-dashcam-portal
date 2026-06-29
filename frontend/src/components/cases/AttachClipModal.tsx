/**
 * Modal for attaching a clip to a case.
 *
 * Deliberately NOT a reuse of the full SearchPage — that pulls in
 * URL-state, debouncing, infinite scroll, the full FilterPanel… all
 * overkill for an "attach" picker. Instead we render an inline mini
 * filter (truck dropdown + from/to date range) and call `useClips`
 * directly. The list shows the first page only (Load more deferred).
 *
 * UX:
 *   - Open with the modal mounted; close via Cancel / Escape / backdrop
 *     click. Clicking "+" on a row fires the parent's `onAttach(clipId)`
 *     and closes immediately — there's no separate confirm step because
 *     the attach itself is idempotent on (case, clip).
 *
 * Loading / empty / error states are inline; the modal stays open on
 * error so the user can retry without losing their filters.
 */

import { useEffect, useId, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { useClips, type ClipFilters } from "@/hooks/useClips";
import { useTrucks } from "@/hooks/useTrucks";
import { ApiError } from "@/lib/api";
import type { ClipRow } from "@/lib/types";
import { cn } from "@/lib/utils";

import { formatDateTime } from "./format";

export type AttachClipModalProps = {
  open: boolean;
  isSubmitting: boolean;
  error: unknown;
  /** Called with the chosen clip id once the user clicks "+". */
  onAttach: (clipId: string) => void;
  onClose: () => void;
};

const emptyFilters: ClipFilters = {
  truckIds: [],
  driverIds: [],
  from: null,
  to: null,
  text: "",
};

export function AttachClipModal({
  open,
  isSubmitting,
  error,
  onAttach,
  onClose,
}: AttachClipModalProps) {
  const titleId = useId();
  const [truckId, setTruckId] = useState<string>("");
  const [from, setFrom] = useState<string>("");
  const [to, setTo] = useState<string>("");

  // Reset the picker each time the modal opens so a stale filter from a
  // previous attach doesn't carry over.
  useEffect(() => {
    if (open) {
      setTruckId("");
      setFrom("");
      setTo("");
    }
  }, [open]);

  // Escape closes — same convention as OpenCaseModal.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !isSubmitting) onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, isSubmitting, onClose]);

  const trucks = useTrucks();

  const filters = useMemo<ClipFilters>(
    () => ({
      ...emptyFilters,
      truckIds: truckId ? [truckId] : [],
      from: from || null,
      to: to || null,
    }),
    [truckId, from, to],
  );

  // `enabled`-style gate: don't fetch the entire clips table when the
  // modal first opens with no filters and the tenant has lots of clips.
  // The picker is for "find a clip you have in mind" rather than
  // browsing — require at least one filter before we hit the wire.
  const hasFilter = Boolean(truckId || from || to);

  const clips = useClips(hasFilter ? filters : emptyFilters);
  const items = (hasFilter ? clips.data?.items : undefined) ?? [];

  if (!open) return null;

  const errorMessage = formatError(error);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      data-testid="attach-clip-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget && !isSubmitting) onClose();
      }}
    >
      <div className="w-full max-w-2xl space-y-4 rounded-lg border border-border bg-background p-6 shadow-lg">
        <header className="flex items-center justify-between">
          <h2 id={titleId} className="text-lg font-semibold tracking-tight">
            Attach clip
          </h2>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onClose}
            disabled={isSubmitting}
            data-testid="attach-clip-cancel"
          >
            Close
          </Button>
        </header>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Truck
            <select
              value={truckId}
              onChange={(e) => setTruckId(e.target.value)}
              data-testid="attach-clip-truck"
              className={inputCn}
            >
              <option value="">Any truck</option>
              {(trucks.data ?? []).map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            From
            <input
              type="datetime-local"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              data-testid="attach-clip-from"
              className={inputCn}
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            To
            <input
              type="datetime-local"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              data-testid="attach-clip-to"
              className={inputCn}
            />
          </label>
        </div>

        {errorMessage !== null ? (
          <p
            role="alert"
            data-testid="attach-clip-error"
            className="rounded-md border border-destructive/50 bg-destructive/5 p-2 text-xs text-destructive"
          >
            {errorMessage}
          </p>
        ) : null}

        <ResultList
          hasFilter={hasFilter}
          isLoading={clips.isLoading && hasFilter}
          isError={clips.isError && hasFilter}
          items={items}
          isSubmitting={isSubmitting}
          onAttach={onAttach}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Result list
// ---------------------------------------------------------------------------

type ResultListProps = {
  hasFilter: boolean;
  isLoading: boolean;
  isError: boolean;
  items: ClipRow[];
  isSubmitting: boolean;
  onAttach: (clipId: string) => void;
};

function ResultList({
  hasFilter,
  isLoading,
  isError,
  items,
  isSubmitting,
  onAttach,
}: ResultListProps) {
  if (!hasFilter) {
    return (
      <p
        data-testid="attach-clip-hint"
        className="rounded-md border border-dashed border-border p-6 text-center text-xs text-muted-foreground"
      >
        Pick a truck or a date range to find clips.
      </p>
    );
  }
  if (isLoading) {
    return (
      <p
        data-testid="attach-clip-loading"
        className="rounded-md border border-dashed border-border p-6 text-center text-xs text-muted-foreground"
      >
        Searching…
      </p>
    );
  }
  if (isError) {
    return (
      <p
        role="alert"
        data-testid="attach-clip-fetch-error"
        className="rounded-md border border-destructive/50 bg-destructive/5 p-2 text-xs text-destructive"
      >
        Couldn’t load clips.
      </p>
    );
  }
  if (items.length === 0) {
    return (
      <p
        data-testid="attach-clip-empty"
        className="rounded-md border border-dashed border-border p-6 text-center text-xs text-muted-foreground"
      >
        No clips match these filters.
      </p>
    );
  }
  return (
    <ul
      data-testid="attach-clip-results"
      className="max-h-72 divide-y divide-border overflow-y-auto rounded-md border border-border"
    >
      {items.map((clip) => (
        <li
          key={clip.id}
          className="flex items-center justify-between gap-3 px-3 py-2"
          data-testid={`attach-clip-row-${clip.id}`}
        >
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">
              {clip.truck_label}
            </div>
            <div className="text-xs text-muted-foreground">
              {formatDateTime(clip.started_at)}
              {clip.driver_name ? ` · ${clip.driver_name}` : ""}
              {` · ${clip.duration_s}s`}
            </div>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={isSubmitting}
            onClick={() => onAttach(clip.id)}
            data-testid={`attach-clip-add-${clip.id}`}
          >
            +
          </Button>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const inputCn = cn(
  "h-9 w-full rounded-md border border-input bg-background px-3 text-sm",
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "disabled:opacity-50",
);

function formatError(err: unknown): string | null {
  if (err === null || err === undefined) return null;
  if (err instanceof ApiError) {
    return `Couldn’t attach clip (${err.status}): ${err.detail}`;
  }
  if (err instanceof Error) return `Couldn’t attach clip: ${err.message}`;
  return "Couldn’t attach clip.";
}
