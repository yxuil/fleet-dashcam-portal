/**
 * Fetch a single clip's detail, including a freshly-minted signed playback URL.
 *
 * The backend issues `GET /clips/{id}?play=true` as the one operation
 * that both returns clip metadata AND writes a `clip.play_url_minted`
 * audit row. We deliberately set `staleTime: 0` so every navigation to
 * `/clips/:id` re-mints the URL: signed URLs are short-lived (see
 * `DEFAULT_SIGNED_URL_TTL_S`), and stale data would mean a 403 from
 * MinIO when the user actually hits play.
 *
 * `gcTime: 0` keeps the cache from holding onto the URL after the page
 * unmounts — there's no scenario where we want to replay a still-live
 * signed URL without going through the audit path again.
 */

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { ClipDetail } from "@/lib/types";

export function useClipDetail(id: string | undefined) {
  return useQuery({
    queryKey: ["clip", id],
    queryFn: () => apiGet<ClipDetail>(`/clips/${id}?play=true`),
    enabled: Boolean(id),
    staleTime: 0,
    gcTime: 0,
  });
}
