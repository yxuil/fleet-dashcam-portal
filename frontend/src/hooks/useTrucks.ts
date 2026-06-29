/**
 * Hook returning the caller's trucks for filter dropdowns.
 *
 * Wraps `GET /trucks` in a React Query. The list rarely changes during
 * a session, so we lean on the shared client's 30s staleTime to keep
 * the multi-select snappy.
 */

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { TruckOut } from "@/lib/types";

export const TRUCKS_QUERY_KEY = ["trucks"] as const;

export function useTrucks() {
  return useQuery<TruckOut[]>({
    queryKey: TRUCKS_QUERY_KEY,
    queryFn: () => apiGet<TruckOut[]>("/trucks"),
  });
}
