/**
 * Hook returning the caller's drivers for filter dropdowns.
 */

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { DriverOut } from "@/lib/types";

export const DRIVERS_QUERY_KEY = ["drivers"] as const;

export function useDrivers() {
  return useQuery<DriverOut[]>({
    queryKey: DRIVERS_QUERY_KEY,
    queryFn: () => apiGet<DriverOut[]>("/drivers"),
  });
}
