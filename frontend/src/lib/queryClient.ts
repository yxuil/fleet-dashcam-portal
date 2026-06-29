/**
 * Shared React Query client.
 *
 * Defaults:
 *   - `staleTime: 30s` so navigating between sibling pages doesn't re-hit
 *     the backend for data we just fetched.
 *   - No retry on 4xx (a 401 / 403 / 404 won't fix itself), one retry on
 *     5xx for transient server flakiness.
 */

import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "./api";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status < 500) {
          return false;
        }
        return failureCount < 1;
      },
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
});
