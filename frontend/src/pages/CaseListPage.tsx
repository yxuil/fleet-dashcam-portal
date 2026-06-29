/**
 * Case list page — `/cases`.
 *
 * Composition:
 *   - Filter bar at top: status multi-select chips, "Assigned to me"
 *     toggle (filters via the current user's id from `useMe`), free-text
 *     `q` search.
 *   - Card list of cases. Each card links to `/cases/:id`.
 *   - "Load more" pagination via `useCasesList`'s `fetchNextPage`.
 *
 * No URL state: a cases listing isn't typically a shared deep-link the
 * way Search is (it's filtered by who's signed in and which work they
 * own); we keep filter state local to the page. If we end up wanting
 * shareable case lists, lift the filters to `useSearchParams` the same
 * way SearchPage does.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { StatusBadge } from "@/components/cases/StatusBadge";
import { formatDateTime } from "@/components/cases/format";
import { Button } from "@/components/ui/Button";
import { useCasesList, type CaseFilters } from "@/hooks/useCasesList";
import { useMe } from "@/hooks/useMe";
import { ApiError } from "@/lib/api";
import {
  CASE_STATUSES,
  type CaseRow,
  type CaseStatus,
} from "@/lib/types";
import { cn } from "@/lib/utils";

import { STATUS_LABEL } from "@/components/cases/format";

export function CaseListPage() {
  const me = useMe();

  // -----------------------------------------------------------------
  // Filter state — local because case lists aren't a deep-link target.
  // -----------------------------------------------------------------
  const [statuses, setStatuses] = useState<readonly CaseStatus[]>([]);
  const [assignedToMe, setAssignedToMe] = useState(false);
  const [q, setQ] = useState("");

  const filters = useMemo<CaseFilters>(
    () => ({
      statuses,
      assigneeUserId: assignedToMe ? (me.data?.user_id ?? null) : null,
      q,
    }),
    [statuses, assignedToMe, me.data?.user_id, q],
  );

  const cases = useCasesList(filters);

  const items = cases.data?.items ?? [];
  const lastPage = cases.data?.pages?.[cases.data.pages.length - 1];
  const hasNextPage = Boolean(lastPage?.next_cursor);

  function toggleStatus(s: CaseStatus): void {
    setStatuses((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s],
    );
  }

  return (
    <section className="space-y-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Cases</h1>
        <p className="text-xs text-muted-foreground">
          {cases.isFetching && !cases.isFetchingNextPage
            ? "Loading…"
            : `${items.length} case${items.length === 1 ? "" : "s"}`}
        </p>
      </header>

      <FilterBar
        statuses={statuses}
        onToggleStatus={toggleStatus}
        assignedToMe={assignedToMe}
        onToggleAssignedToMe={() => setAssignedToMe((v) => !v)}
        q={q}
        onQChange={setQ}
      />

      <Results
        isLoading={cases.isLoading}
        isError={cases.isError}
        error={cases.error}
        items={items}
        onRetry={() => cases.refetch()}
      />

      {hasNextPage ? (
        <div className="flex justify-center pt-2">
          <Button
            variant="outline"
            size="sm"
            disabled={cases.isFetchingNextPage}
            onClick={() => cases.fetchNextPage()}
            data-testid="cases-load-more"
          >
            {cases.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      ) : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Filter bar
// ---------------------------------------------------------------------------

type FilterBarProps = {
  statuses: readonly CaseStatus[];
  onToggleStatus: (s: CaseStatus) => void;
  assignedToMe: boolean;
  onToggleAssignedToMe: () => void;
  q: string;
  onQChange: (next: string) => void;
};

function FilterBar({
  statuses,
  onToggleStatus,
  assignedToMe,
  onToggleAssignedToMe,
  q,
  onQChange,
}: FilterBarProps) {
  return (
    <div
      data-testid="cases-filter-bar"
      className="flex flex-col gap-3 rounded-md border border-border bg-card p-4 sm:flex-row sm:items-end"
    >
      <div className="flex-1 space-y-2">
        <label className="block text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Status
        </label>
        <div className="flex flex-wrap gap-1.5">
          {CASE_STATUSES.map((s) => {
            const active = statuses.includes(s);
            return (
              <button
                key={s}
                type="button"
                onClick={() => onToggleStatus(s)}
                aria-pressed={active}
                data-testid={`cases-status-chip-${s}`}
                className={cn(
                  "rounded-full border px-2.5 py-0.5 text-[11px] font-medium",
                  active
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-muted/40 text-muted-foreground hover:bg-accent",
                )}
              >
                {STATUS_LABEL[s]}
              </button>
            );
          })}
        </div>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={assignedToMe}
          onChange={onToggleAssignedToMe}
          data-testid="cases-assigned-to-me"
        />
        Assigned to me
      </label>

      <div className="flex-1 space-y-2">
        <label className="block text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Search
        </label>
        <input
          type="search"
          value={q}
          onChange={(e) => onQChange(e.target.value)}
          placeholder="Number, requester, ref…"
          data-testid="cases-q"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>
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
  items: CaseRow[];
  onRetry: () => void;
};

function Results({ isLoading, isError, error, items, onRetry }: ResultsProps) {
  if (isLoading) {
    return (
      <ul
        data-testid="cases-skeleton"
        className="grid grid-cols-1 gap-3 md:grid-cols-2"
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <li
            key={i}
            className="h-20 animate-pulse rounded-md border border-border bg-muted"
          />
        ))}
      </ul>
    );
  }
  if (isError) {
    const status = error instanceof ApiError ? ` (${error.status})` : "";
    return (
      <div
        role="alert"
        data-testid="cases-error"
        className="flex flex-col items-start gap-2 rounded-md border border-destructive/50 bg-destructive/5 p-4 text-sm"
      >
        <p className="font-medium text-destructive">
          Couldn’t load cases{status}.
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
        data-testid="cases-empty"
        className="rounded-md border border-dashed border-border p-8 text-center text-sm text-muted-foreground"
      >
        No cases match these filters.
      </div>
    );
  }
  return (
    <ul
      data-testid="cases-list"
      className="grid grid-cols-1 gap-3 md:grid-cols-2"
    >
      {items.map((c) => (
        <li key={c.id}>
          <CaseCard caseRow={c} />
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------
// One card
// ---------------------------------------------------------------------------

function CaseCard({ caseRow }: { caseRow: CaseRow }) {
  return (
    <Link
      to={`/cases/${caseRow.id}`}
      data-testid={`cases-card-${caseRow.id}`}
      className="block rounded-md border border-border bg-card p-4 shadow-sm transition-shadow hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-sm font-semibold">{caseRow.number}</span>
        <StatusBadge status={caseRow.status} />
      </div>
      <div className="mt-2 text-sm">
        <span data-testid={`cases-card-requester-${caseRow.id}`}>
          {caseRow.requester_name ?? "—"}
        </span>
        {caseRow.requester_org ? (
          <>
            <span aria-hidden> · </span>
            <span className="text-muted-foreground">
              {caseRow.requester_org}
            </span>
          </>
        ) : null}
      </div>
      <div className="mt-1 text-xs text-muted-foreground">
        Incident: {formatDateTime(caseRow.incident_at)}
      </div>
    </Link>
  );
}
