/**
 * Triage mutation for a single event.
 *
 * Wraps `POST /events/:id/triage` so the timeline page can mark an event
 * as `false_positive`, `coaching_note`, or `open_case`. The backend
 * never mutates the event row — the label/note land in the audit log
 * only — so this hook deliberately doesn't try to invalidate the
 * events list query. The caller marks the row in local state instead.
 *
 * Two shapes returned to keep the call site small:
 *   - `triageLabel`: the last successfully-applied label, surfaced as
 *     an inline badge in the row.
 *   - `mutateAsync`: the underlying mutation, available for the
 *     open-case flow which needs to await the audit before navigating.
 */

import { useMutation } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { apiPost } from "@/lib/api";
import type { EventRow } from "@/lib/types";

export type TriageLabel = "false_positive" | "coaching_note" | "open_case";

export type TriageBody = {
  label: TriageLabel;
  note?: string;
};

export type UseTriageOptions = {
  onSuccess?: (label: TriageLabel) => void;
  onError?: (error: unknown) => void;
};

export function useTriage(eventId: string, opts: UseTriageOptions = {}) {
  // Last successfully-applied label, used to render the inline badge.
  const [triageLabel, setTriageLabel] = useState<TriageLabel | null>(null);

  const mutation = useMutation({
    mutationFn: (body: TriageBody) =>
      apiPost<EventRow>(`/events/${eventId}/triage`, body),
    onSuccess: (_data, variables) => {
      setTriageLabel(variables.label);
      opts.onSuccess?.(variables.label);
    },
    onError: (err) => {
      opts.onError?.(err);
    },
  });

  // Convenience wrapper so call sites don't need to construct the body.
  const triage = useCallback(
    (label: TriageLabel, note?: string) =>
      mutation.mutate({ label, note }),
    [mutation],
  );

  return {
    triage,
    triageLabel,
    isPending: mutation.isPending,
    isError: mutation.isError,
    error: mutation.error,
    mutateAsync: mutation.mutateAsync,
  };
}
