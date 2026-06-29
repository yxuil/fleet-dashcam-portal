/**
 * React Query wrapper for `GET /me/preferences`.
 *
 * Long stale time + cross-page sharing via the `me-prefs` query key —
 * the Fleet Cam page reads the same cache the reorder mutation
 * `setQueryData`s into.
 */

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { Preferences } from "@/lib/types";

export const PREFS_QUERY_KEY = ["me-prefs"] as const;

export function usePrefs() {
  return useQuery<Preferences>({
    queryKey: PREFS_QUERY_KEY,
    queryFn: () => apiGet<Preferences>("/me/preferences"),
  });
}
