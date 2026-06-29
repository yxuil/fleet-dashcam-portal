/**
 * Create-case-and-attach-clip mutation.
 *
 * Two-step sequence wrapped in one mutation:
 *   1. `POST /cases` with `{ external_ref?, requester_name?, requester_org?,
 *      incident_at }` → returns the new case detail (incl. its id).
 *   2. `POST /cases/:newId/clips` with `{ clip_id, note }` to attach the
 *      source clip immediately. Idempotent on (case, clip) on the
 *      backend, so a retry doesn't double-attach.
 *
 * The hook returns the final `CaseDetail` (after attachment), so the
 * caller can grab `data.id` and navigate to `/cases/:id`. If either
 * step fails, the mutation rejects and the modal stays open with the
 * error visible.
 *
 * Note: we intentionally do NOT call `POST /events/:id/triage` from
 * here — the timeline page does that separately, so a failed open-case
 * flow doesn't leave a dangling "open_case" audit row when the case
 * itself was never created.
 */

import { useMutation } from "@tanstack/react-query";

import { apiPost } from "@/lib/api";

export type CaseCreateBody = {
  external_ref?: string | null;
  requester_name?: string | null;
  requester_org?: string | null;
  incident_at?: string | null;
};

export type CreateCaseAndAttachInput = {
  case: CaseCreateBody;
  clipId: string;
  attachNote?: string;
};

/**
 * Minimal subset of `CaseDetail` we use after creation.
 * The full shape isn't worth typing here — only `id` matters for the
 * follow-up navigation.
 */
export type CreatedCase = {
  id: string;
  number: string;
  tenant_id: string;
};

export function useCreateCase() {
  return useMutation({
    mutationFn: async (input: CreateCaseAndAttachInput): Promise<CreatedCase> => {
      const created = await apiPost<CreatedCase>("/cases", input.case);
      await apiPost<CreatedCase>(`/cases/${created.id}/clips`, {
        clip_id: input.clipId,
        note: input.attachNote ?? null,
      });
      return created;
    },
  });
}
