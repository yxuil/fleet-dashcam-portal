/**
 * Case-level mutations: patch, attach clip, close, add note.
 *
 * Each mutation:
 *   - Calls the matching backend endpoint via the typed API helper.
 *   - On success, sets `["case", id]` in the React Query cache to the
 *     fresh `CaseDetail` returned by the server (patch/attach/close
 *     already return the new detail). For the note mutation (which is
 *     a 204), we invalidate so the next render re-fetches the audit.
 *
 * All mutations are deliberately scoped to one `caseId` per hook
 * instance so call sites read like:
 *
 *   const patch = usePatchCase(case.id);
 *   patch.mutate({ status: "under_review" });
 *
 * which avoids passing the id through the mutation body every time.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiPost, apiPatch } from "@/lib/api";
import type {
  CaseDetail,
  PatchableCaseStatus,
} from "@/lib/types";

import { caseDetailQueryKey } from "./useCaseDetail";

// ---------------------------------------------------------------------------
// PATCH /cases/:id
// ---------------------------------------------------------------------------

export type CasePatchBody = {
  status?: PatchableCaseStatus;
  assignee_user_id?: string | null;
  due_at?: string | null;
  external_ref?: string | null;
  requester_name?: string | null;
  requester_org?: string | null;
  incident_at?: string | null;
};

export function usePatchCase(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CasePatchBody) =>
      apiPatch<CaseDetail>(`/cases/${caseId}`, body),
    onSuccess: (data) => {
      qc.setQueryData(caseDetailQueryKey(caseId), data);
      // List view is keyed by filters; cheaper to invalidate everything
      // tagged "cases" than to surgically update each cached page.
      void qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });
}

// ---------------------------------------------------------------------------
// POST /cases/:id/clips
// ---------------------------------------------------------------------------

export type AttachClipBody = {
  clip_id: string;
  note?: string | null;
};

export function useAttachClip(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AttachClipBody) =>
      apiPost<CaseDetail>(`/cases/${caseId}/clips`, body),
    onSuccess: (data) => {
      qc.setQueryData(caseDetailQueryKey(caseId), data);
    },
  });
}

// ---------------------------------------------------------------------------
// POST /cases/:id/close
// ---------------------------------------------------------------------------

export type CloseCaseBody = {
  reason: string;
};

export function useCloseCase(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CloseCaseBody) =>
      apiPost<CaseDetail>(`/cases/${caseId}/close`, body),
    onSuccess: (data) => {
      qc.setQueryData(caseDetailQueryKey(caseId), data);
      void qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });
}

// ---------------------------------------------------------------------------
// POST /cases/:id/audit  (notes-as-audit, see T14 design notes)
// ---------------------------------------------------------------------------

export type AddCaseNoteBody = {
  text: string;
};

export function useAddCaseNote(caseId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: AddCaseNoteBody): Promise<void> => {
      // The backend returns 204; the api wrapper resolves to `undefined`.
      await apiPost<void>(`/cases/${caseId}/audit`, {
        action: "case.note_added",
        payload: { text: body.text },
      });
    },
    onSuccess: () => {
      // Notes are reflected via recent_audit on the detail, so we have
      // to re-fetch — no compact payload to merge in.
      void qc.invalidateQueries({ queryKey: caseDetailQueryKey(caseId) });
    },
  });
}
