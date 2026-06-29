/**
 * Search page — `/search`.
 *
 * Composition:
 *   - URL search params hold the filter state, so a search is shareable
 *     and a reload preserves what you were looking at.
 *   - Filter edits update the URL immediately (so links & back/forward
 *     work), then a 300ms debounce gates the actual `GET /clips` call.
 *   - The sidebar `<FilterPanel />` owns the filter UI; this page glues
 *     state, debouncing, and the results grid together.
 *
 * Why URL params + a debounced mirror, not React state?
 *   - "URL is the source of truth" means deep links and reloads always
 *     show the same results — important for sharing a search with a
 *     teammate, which is the whole point of a Search page.
 *   - The debounced mirror is purely about avoiding one request per
 *     keystroke. It only flows into React Query, never back into the URL.
 */

import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { ClipCard } from "@/components/search/ClipCard";
import { FilterPanel } from "@/components/search/FilterPanel";
import { Button } from "@/components/ui/Button";
import { useClips, type ClipFilters, EMPTY_FILTERS } from "@/hooks/useClips";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useDrivers } from "@/hooks/useDrivers";
import { useTrucks } from "@/hooks/useTrucks";
import { ApiError } from "@/lib/api";

const DEBOUNCE_MS = 300;

// ---------------------------------------------------------------------------
// URL <-> filters serialization
// ---------------------------------------------------------------------------

const PARAM_TRUCK = "truck";
const PARAM_DRIVER = "driver";
const PARAM_FROM = "from";
const PARAM_TO = "to";
const PARAM_TEXT = "q";

function parseFilters(params: URLSearchParams): ClipFilters {
  return {
    truckIds: params.getAll(PARAM_TRUCK),
    driverIds: params.getAll(PARAM_DRIVER),
    from: params.get(PARAM_FROM),
    to: params.get(PARAM_TO),
    text: params.get(PARAM_TEXT) ?? "",
  };
}

function filtersToParams(filters: ClipFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const id of filters.truckIds) params.append(PARAM_TRUCK, id);
  for (const id of filters.driverIds) params.append(PARAM_DRIVER, id);
  if (filters.from) params.set(PARAM_FROM, filters.from);
  if (filters.to) params.set(PARAM_TO, filters.to);
  if (filters.text.trim()) params.set(PARAM_TEXT, filters.text);
  return params;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function SearchPage() {
  const [searchParams, setSearchParams] = useSearchParams();

  // Reconstruct filters from URL every render so back/forward / reload /
  // direct-link all work. JSON.stringify in `useDebouncedValue` makes
  // the resulting reference churn cheap.
  const filters: ClipFilters = useMemo(
    () => parseFilters(searchParams),
    [searchParams],
  );

  const debouncedFilters = useDebouncedValue(filters, DEBOUNCE_MS);

  const setFilters = useCallback(
    (next: ClipFilters) => {
      setSearchParams(filtersToParams(next), { replace: true });
    },
    [setSearchParams],
  );

  const trucks = useTrucks();
  const drivers = useDrivers();
  const clips = useClips(debouncedFilters);

  const items = clips.data?.items ?? [];
  const lastPage = clips.data?.pages?.[clips.data.pages.length - 1];
  const hasNextPage = Boolean(lastPage?.next_cursor);

  return (
    <section className="space-y-6">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Search</h1>
        <p className="text-xs text-muted-foreground">
          {clips.isFetching && !clips.isFetchingNextPage
            ? "Searching…"
            : `${items.length} result${items.length === 1 ? "" : "s"}`}
        </p>
      </header>

      <div className="flex flex-col gap-6 lg:flex-row">
        <FilterPanel
          filters={filters}
          onChange={setFilters}
          trucks={trucks.data ?? []}
          drivers={drivers.data ?? []}
          trucksLoading={trucks.isLoading}
          driversLoading={drivers.isLoading}
        />

        <div className="flex-1 space-y-4">
          <Results
            isLoading={clips.isLoading}
            isError={clips.isError}
            error={clips.error}
            items={items}
            onRetry={() => clips.refetch()}
            onClear={() => setFilters(EMPTY_FILTERS)}
          />

          {hasNextPage ? (
            <div className="flex justify-center pt-2">
              <Button
                variant="outline"
                size="sm"
                disabled={clips.isFetchingNextPage}
                onClick={() => clips.fetchNextPage()}
                data-testid="load-more"
              >
                {clips.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Results grid + loading / empty / error states
// ---------------------------------------------------------------------------

type ResultsProps = {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  items: ReturnType<typeof useClips>["data"] extends infer T
    ? T extends { items: infer I }
      ? I
      : never
    : never;
  onRetry: () => void;
  onClear: () => void;
};

function Results({
  isLoading,
  isError,
  error,
  items,
  onRetry,
  onClear,
}: ResultsProps) {
  if (isLoading) {
    return <SkeletonGrid />;
  }
  if (isError) {
    const status = error instanceof ApiError ? ` (${error.status})` : "";
    return (
      <div
        role="alert"
        data-testid="clips-error"
        className="flex flex-col items-start gap-2 rounded-md border border-destructive/50 bg-destructive/5 p-4 text-sm"
      >
        <p className="font-medium text-destructive">
          Couldn’t load clips{status}.
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
        data-testid="clips-empty"
        className="rounded-md border border-dashed border-border p-8 text-center text-sm text-muted-foreground"
      >
        <p>No clips match these filters.</p>
        <button
          type="button"
          onClick={onClear}
          className="mt-2 text-xs underline underline-offset-2 hover:text-foreground"
        >
          Clear filters
        </button>
      </div>
    );
  }
  return (
    <div
      data-testid="clips-grid"
      className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
    >
      {items.map((clip) => (
        <ClipCard key={clip.id} clip={clip} />
      ))}
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          data-testid="clip-skeleton"
          className="overflow-hidden rounded-lg border border-border bg-card"
        >
          <div className="aspect-video w-full animate-pulse bg-muted" />
          <div className="space-y-2 p-3">
            <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
            <div className="h-2 w-1/2 animate-pulse rounded bg-muted" />
          </div>
        </div>
      ))}
    </div>
  );
}
