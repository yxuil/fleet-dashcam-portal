/**
 * Fetch a single case's detail (header + attached clips + recent audit).
 *
 * Wraps `GET /cases/:id`. The query key is `["case", id]` so mutations
 * (patch, close, attach, note) can invalidate or set this entry to
 * surface updated state without an extra round-trip.
 */

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { CaseDetail } from "@/lib/types";

/** Query-key factory shared with mutations that invalidate this query. */
export function caseDetailQueryKey(id: string | undefined): readonly unknown[] {
  return ["case", id];
}

export function useCaseDetail(id: string | undefined) {
  return useQuery({
    queryKey: caseDetailQueryKey(id),
    queryFn: () => apiGet<CaseDetail>(`/cases/${id}`),
    enabled: Boolean(id),
  });
}
