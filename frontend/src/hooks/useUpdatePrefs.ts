/**
 * Optimistic mutation for `PATCH /me/preferences`.
 *
 * The mutation:
 *   1. `onMutate` — overlay the patch on the cached prefs so the UI
 *      reorders immediately.
 *   2. `onSuccess` — replace the cache with the backend's merged
 *      response (covers the case where the server rewrote / added keys).
 *   3. `onError` — invalidate so the cache resyncs with the truth.
 *
 * TanStack Query queues concurrent mutations; if the user rapid-clicks
 * the reorder arrows we'll fire one PATCH per click, and the last
 * server response wins — that's intentional.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiPatch } from "@/lib/api";
import type { Preferences } from "@/lib/types";

import { PREFS_QUERY_KEY } from "./usePrefs";

export function useUpdatePrefs() {
  const qc = useQueryClient();

  return useMutation<Preferences, Error, Partial<Preferences>>({
    mutationFn: (patch) => apiPatch<Preferences>("/me/preferences", patch),
    onMutate: (patch) => {
      // Snapshot in case we need to roll back on error.
      const previous = qc.getQueryData<Preferences>(PREFS_QUERY_KEY);
      qc.setQueryData<Preferences>(PREFS_QUERY_KEY, (prev) => ({
        ...(prev ?? {}),
        ...patch,
      }));
      return { previous };
    },
    onSuccess: (response) => {
      qc.setQueryData<Preferences>(PREFS_QUERY_KEY, response);
    },
    onError: () => {
      // Pull the truth from the server — refetching is simpler than
      // teaching the cache the inverse of our optimistic write.
      qc.invalidateQueries({ queryKey: PREFS_QUERY_KEY });
    },
  });
}
