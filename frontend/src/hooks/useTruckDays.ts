/**
 * React Query wrapper for `GET /trucks/{id}/days`.
 *
 * Feeds the Fleet Cam horizontal day-card scroller. We accept the
 * filter as a plain object so the query key correctly invalidates when
 * the date range or driver narrows.
 */

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { TruckDay } from "@/lib/types";

export type TruckDaysFilters = {
  from?: string | null;
  to?: string | null;
  driver_id?: string | null;
  limit?: number;
};

export function useTruckDays(truckId: string, filters: TruckDaysFilters) {
  const qs = new URLSearchParams();
  if (filters.from) qs.set("from", filters.from);
  if (filters.to) qs.set("to", filters.to);
  if (filters.driver_id) qs.set("driver_id", filters.driver_id);
  if (filters.limit) qs.set("limit", String(filters.limit));
  const query = qs.toString();

  return useQuery<TruckDay[]>({
    queryKey: ["truck-days", truckId, filters],
    queryFn: () =>
      apiGet<TruckDay[]>(
        query ? `/trucks/${truckId}/days?${query}` : `/trucks/${truckId}/days`,
      ),
    // The clip rollup is stable enough that the row doesn't need to
    // refetch on every keystroke in the picker; the 60s window keeps
    // typing feeling snappy.
    staleTime: 60_000,
  });
}
