/**
 * Events tied to a specific clip — drives the timeline markers on the
 * video-player page.
 *
 * We rely on the T12 backend extension to `GET /events?clip_id=...` so
 * the query is server-side filtered (no client-side N+1 across all
 * tenant events).
 *
 * The page only needs the first page: a single dashcam clip is at most
 * a minute or two long, so the number of harsh events linked to it is
 * tiny by construction. If that ever stops being true we can revisit
 * with `useInfiniteQuery`.
 */

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { EventListResponse } from "@/lib/types";

export function useClipEvents(clipId: string | undefined) {
  return useQuery({
    queryKey: ["clip-events", clipId],
    queryFn: () =>
      apiGet<EventListResponse>(`/events?clip_id=${clipId}`),
    enabled: Boolean(clipId),
  });
}
