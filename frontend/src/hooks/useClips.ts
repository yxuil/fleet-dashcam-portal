/**
 * Cursor-paginated clips query keyed by filter state.
 *
 * Uses TanStack's `useInfiniteQuery` so the SearchPage can render a
 * "Load more" button without reimplementing cursor bookkeeping. The
 * page param is the opaque cursor string the backend returns; the
 * first page passes `undefined`.
 *
 * Filter shape — see `ClipFilters` — supports the subset that
 * `GET /clips` actually accepts (truck_id, driver_id, from, to, text).
 * Event-type / severity chips are present in the filter UI but
 * deliberately ignored here: they're "coming in v2" per T11 scope.
 *
 * The backend accepts a single `truck_id` / `driver_id`. To honour
 * multi-select without changing the endpoint, when the user picks
 * exactly one we send it; when they pick zero we send nothing; when
 * they pick more than one we send the first and filter the rest
 * client-side. (For the seeded fleets a tenant has a handful of trucks
 * so this is fine for the MVP. A follow-up task can extend the endpoint
 * to accept comma-separated ids.)
 */

import { useInfiniteQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { ClipListResponse, ClipRow } from "@/lib/types";

export type ClipFilters = {
  truckIds: readonly string[];
  driverIds: readonly string[];
  from: string | null;
  to: string | null;
  text: string;
};

export const EMPTY_FILTERS: ClipFilters = {
  truckIds: [],
  driverIds: [],
  from: null,
  to: null,
  text: "",
};

const PAGE_SIZE = 24;

function buildQuery(filters: ClipFilters, cursor: string | null): string {
  const params = new URLSearchParams();
  if (filters.truckIds.length === 1) {
    params.set("truck_id", filters.truckIds[0]);
  }
  if (filters.driverIds.length === 1) {
    params.set("driver_id", filters.driverIds[0]);
  }
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.text.trim()) params.set("text", filters.text.trim());
  params.set("limit", String(PAGE_SIZE));
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

/**
 * Apply client-side post-filtering for multi-select id fields the
 * backend doesn't yet support natively (it accepts a single id only).
 */
function postFilter(items: ClipRow[], filters: ClipFilters): ClipRow[] {
  let out = items;
  if (filters.truckIds.length > 1) {
    const set = new Set(filters.truckIds);
    out = out.filter((c) => set.has(c.truck_id));
  }
  if (filters.driverIds.length > 1) {
    const set = new Set(filters.driverIds);
    out = out.filter((c) => (c.driver_id ? set.has(c.driver_id) : false));
  }
  return out;
}

export function useClips(filters: ClipFilters) {
  return useInfiniteQuery({
    queryKey: ["clips", filters],
    queryFn: async ({ pageParam }) => {
      const qs = buildQuery(filters, (pageParam as string | null) ?? null);
      return apiGet<ClipListResponse>(`/clips?${qs}`);
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    // Flatten + client-filter through `select` so consumers get a
    // single `items` array and a `hasNextPage` flag.
    select: (data) => ({
      items: postFilter(
        data.pages.flatMap((p) => p.items),
        filters,
      ),
      pages: data.pages,
      pageParams: data.pageParams,
    }),
  });
}
