/**
 * Hook returning the authenticated principal.
 *
 * Wraps `GET /me` in a React Query so the top bar, sidebar, and any
 * tenant-aware component can read the same cached result.
 */

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { Principal } from "@/lib/auth";

export const ME_QUERY_KEY = ["me"] as const;

export function useMe() {
  return useQuery<Principal>({
    queryKey: ME_QUERY_KEY,
    queryFn: () => apiGet<Principal>("/me"),
    // `/me` is cheap and dev users switch by reload anyway — keep the
    // default 30s staleTime from the shared client.
  });
}
