/**
 * Cursor-paginated events query for the timeline pages.
 *
 * Used by `/trucks/:id/events` and `/drivers/:id/events`. Wraps
 * `GET /events` with truck_id / driver_id / severity[] / type[] filters.
 *
 * Backend filter notes
 * --------------------
 * * ``truck_id`` is a direct exact-match on the event row.
 * * ``driver_id`` joins `clips` and matches `clips.driver_id`. Events
 *   without an attached clip are excluded — they can't be attributed to
 *   a driver. (T13 added this server-side; before, ``driver_id`` was a
 *   documented no-op.)
 * * ``severity`` and ``type`` are repeatable params; "All" / empty means
 *   no filter on that dimension.
 *
 * The hook intentionally accepts both `truckId` and `driverId` so a
 * caller can scope to either dimension without instantiating a second
 * hook. They are mutually exclusive at the page level (the page picks
 * one), but nothing here enforces that — the backend resolves any odd
 * combination by AND-ing the filters.
 */

import { useInfiniteQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type {
  EventListResponse,
  EventSeverity,
  EventType,
} from "@/lib/types";

export type TimelineEventFilters = {
  truckId?: string;
  driverId?: string;
  severities: readonly EventSeverity[];
  types: readonly EventType[];
};

const PAGE_SIZE = 50;

function buildQuery(filters: TimelineEventFilters, cursor: string | null): string {
  const params = new URLSearchParams();
  if (filters.truckId) params.set("truck_id", filters.truckId);
  if (filters.driverId) params.set("driver_id", filters.driverId);
  for (const s of filters.severities) params.append("severity", s);
  for (const t of filters.types) params.append("type", t);
  params.set("limit", String(PAGE_SIZE));
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

export function useTimelineEvents(filters: TimelineEventFilters) {
  // Enabled only when we have a scope (truck or driver). The
  // EventTimelinePage routes are scoped by URL, so this is mostly
  // defensive — `useParams` could in theory hand us `undefined` if the
  // route shape ever changes.
  const enabled = Boolean(filters.truckId) || Boolean(filters.driverId);

  return useInfiniteQuery({
    queryKey: ["timeline-events", filters],
    queryFn: async ({ pageParam }) => {
      const qs = buildQuery(filters, (pageParam as string | null) ?? null);
      return apiGet<EventListResponse>(`/events?${qs}`);
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled,
    select: (data) => ({
      items: data.pages.flatMap((p) => p.items),
      pages: data.pages,
      pageParams: data.pageParams,
    }),
  });
}
