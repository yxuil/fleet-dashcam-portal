/**
 * Cursor-paginated cases query keyed by filter state.
 *
 * Wraps `GET /cases?status=&assignee_user_id=&q=&limit=&cursor=` with
 * TanStack's `useInfiniteQuery`. The backend takes `?status=` as a
 * repeatable parameter — pass each selected status as its own
 * `status=` entry on the URL — so multi-select chips work natively.
 *
 * The page param is the opaque cursor string the backend returns; the
 * first page passes `undefined`.
 */

import { useInfiniteQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { CaseListResponse, CaseStatus } from "@/lib/types";

export type CaseFilters = {
  /** Empty array → no status filter; otherwise OR'd by the backend. */
  statuses: readonly CaseStatus[];
  /** Filter to cases assigned to this user id. `null` → no filter. */
  assigneeUserId: string | null;
  /** Free-text substring across number / external_ref / requester_*. */
  q: string;
};

export const EMPTY_CASE_FILTERS: CaseFilters = {
  statuses: [],
  assigneeUserId: null,
  q: "",
};

const PAGE_SIZE = 24;

function buildQuery(filters: CaseFilters, cursor: string | null): string {
  const params = new URLSearchParams();
  for (const s of filters.statuses) params.append("status", s);
  if (filters.assigneeUserId) {
    params.set("assignee_user_id", filters.assigneeUserId);
  }
  if (filters.q.trim()) params.set("q", filters.q.trim());
  params.set("limit", String(PAGE_SIZE));
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

export function useCasesList(filters: CaseFilters) {
  return useInfiniteQuery({
    queryKey: ["cases", filters],
    queryFn: async ({ pageParam }) => {
      const qs = buildQuery(filters, (pageParam as string | null) ?? null);
      return apiGet<CaseListResponse>(`/cases?${qs}`);
    },
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    // Flatten so consumers see one `items` array; keep raw `pages` for
    // the "Load more" affordance.
    select: (data) => ({
      items: data.pages.flatMap((p) => p.items),
      pages: data.pages,
      pageParams: data.pageParams,
    }),
  });
}
