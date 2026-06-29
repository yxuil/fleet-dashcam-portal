/**
 * Tests for `<CaseDetailPage />`.
 *
 * The data hook and the mutation hooks are mocked so we can verify the
 * page wires user actions to the right mutations. Tab switching, note
 * appending, status change, attach flow, and close flow are all covered.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CaseDetail } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mocks — declared before importing the page so it picks them up.
// ---------------------------------------------------------------------------

const useCaseDetailMock = vi.fn();
vi.mock("@/hooks/useCaseDetail", () => ({
  useCaseDetail: (...args: unknown[]) => useCaseDetailMock(...args),
  caseDetailQueryKey: (id: string | undefined) => ["case", id] as const,
}));

const patchMutate = vi.fn();
const attachMutate = vi.fn();
const closeMutate = vi.fn();
const addNoteMutate = vi.fn();

vi.mock("@/hooks/useCaseMutations", () => ({
  usePatchCase: () => ({
    mutate: patchMutate,
    isPending: false,
    error: null,
  }),
  useAttachClip: () => ({
    mutate: attachMutate,
    isPending: false,
    error: null,
  }),
  useCloseCase: () => ({
    mutate: closeMutate,
    isPending: false,
    error: null,
  }),
  useAddCaseNote: () => ({
    mutate: addNoteMutate,
    isPending: false,
    error: null,
  }),
}));

// The Attach modal pulls in trucks + clips hooks; stub those so we
// don't have to render a full filtered list.
vi.mock("@/hooks/useTrucks", () => ({
  useTrucks: () => ({
    data: [{ id: "t1", tenant_id: "tn", label: "Truck-1", vin: null, dashcam_serial: null, last_seen_at: null }],
    isLoading: false,
  }),
}));
vi.mock("@/hooks/useClips", async () => {
  const actual = await vi.importActual<typeof import("@/hooks/useClips")>(
    "@/hooks/useClips",
  );
  return {
    ...actual,
    useClips: () => ({
      data: {
        items: [
          {
            id: "clip-99",
            tenant_id: "tn",
            truck_id: "t1",
            truck_label: "Truck-1",
            driver_id: null,
            driver_name: null,
            started_at: "2026-06-12T14:00:00Z",
            ended_at: "2026-06-12T14:00:30Z",
            duration_s: 30,
            storage_key: "tn/2026/06/12/x.mp4",
            ingested_at: "2026-06-12T14:01:00Z",
          },
        ],
        pages: [],
        pageParams: [],
      },
      isLoading: false,
      isError: false,
    }),
  };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

import { CaseDetailPage } from "./CaseDetailPage";

function makeDetail(overrides: Partial<CaseDetail> = {}): CaseDetail {
  return {
    id: "case-1",
    tenant_id: "tn",
    number: "C-2026-0001",
    external_ref: "TIK-123",
    requester_name: "Alice",
    requester_org: "Acme",
    incident_at: "2026-06-12T14:00:00Z",
    status: "open",
    assignee_user_id: null,
    due_at: null,
    created_by: "u1",
    created_at: "2026-06-12T15:00:00Z",
    clips: [],
    recent_audit: [],
    ...overrides,
  };
}

function setDetail(detail: CaseDetail, extra: Partial<{ isLoading: boolean; isError: boolean; error: unknown }> = {}) {
  useCaseDetailMock.mockReturnValue({
    data: detail,
    isLoading: extra.isLoading ?? false,
    isError: extra.isError ?? false,
    error: extra.error ?? null,
    refetch: vi.fn(),
  });
}

function renderPage(detail: CaseDetail = makeDetail()) {
  setDetail(detail);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[`/cases/${detail.id}`]}>
        <Routes>
          <Route path="/cases/:id" element={<CaseDetailPage />} />
          <Route
            path="/clips/:id"
            element={<div data-testid="clip-detail-page">clip detail</div>}
          />
          <Route
            path="/cases"
            element={<div data-testid="case-list-page">case list</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CaseDetailPage", () => {
  beforeEach(() => {
    useCaseDetailMock.mockReset();
    patchMutate.mockReset();
    attachMutate.mockReset();
    closeMutate.mockReset();
    addNoteMutate.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the header with number, status, identity fields", () => {
    renderPage(
      makeDetail({
        requester_org: "Acme",
        external_ref: "TIK-456",
        assignee_user_id: "u-789",
      }),
    );
    expect(screen.getByTestId("case-detail-number")).toHaveTextContent(
      "C-2026-0001",
    );
    expect(screen.getByTestId("case-status-open")).toBeInTheDocument();
    expect(screen.getByTestId("case-detail-external-ref")).toHaveTextContent(
      "TIK-456",
    );
    expect(screen.getByTestId("case-detail-requester")).toHaveTextContent(
      "Alice",
    );
    expect(screen.getByTestId("case-detail-assignee")).toHaveTextContent(
      "u-789",
    );
  });

  it("switches between Evidence / Notes / Activity tabs", () => {
    renderPage();
    // Evidence is the default.
    expect(screen.getByTestId("case-detail-evidence-empty")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("case-detail-tab-notes"));
    expect(screen.getByTestId("case-detail-notes")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("case-detail-tab-activity"));
    expect(screen.getByTestId("case-detail-activity-empty")).toBeInTheDocument();
  });

  it("status change calls usePatchCase with the new status", () => {
    renderPage();
    fireEvent.change(screen.getByTestId("case-detail-status-select"), {
      target: { value: "under_review" },
    });
    expect(patchMutate).toHaveBeenCalledWith({ status: "under_review" });
  });

  it("Close button opens the close modal and POSTs the reason", async () => {
    renderPage();
    fireEvent.click(screen.getByTestId("case-detail-close"));
    expect(screen.getByTestId("close-case-modal")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("close-case-reason"), {
      target: { value: "We're done." },
    });
    fireEvent.click(screen.getByTestId("close-case-submit"));

    await waitFor(() => {
      expect(closeMutate).toHaveBeenCalled();
    });
    const call = closeMutate.mock.calls[0]?.[0];
    expect(call?.reason).toBe("We're done.");
  });

  it("Notes tab: posting a note calls useAddCaseNote and the audit list renders entries", () => {
    // Render with an existing note in recent_audit.
    renderPage(
      makeDetail({
        recent_audit: [
          {
            id: 1,
            tenant_id: "tn",
            actor_user_id: "u1",
            action: "case.note_added",
            target_type: "case",
            target_id: "case-1",
            payload: { text: "first breadcrumb" },
            occurred_at: "2026-06-12T16:00:00Z",
          },
          {
            id: 2,
            tenant_id: "tn",
            actor_user_id: "u1",
            action: "case.created",
            target_type: "case",
            target_id: "case-1",
            payload: { number: "C-2026-0001" },
            occurred_at: "2026-06-12T15:00:00Z",
          },
        ],
      }),
    );

    fireEvent.click(screen.getByTestId("case-detail-tab-notes"));
    // Existing note is visible (filtered from audit).
    expect(screen.getByTestId("case-detail-note-1")).toHaveTextContent(
      "first breadcrumb",
    );

    // Type and submit.
    fireEvent.change(screen.getByTestId("case-detail-note-input"), {
      target: { value: "Looking good." },
    });
    fireEvent.click(screen.getByTestId("case-detail-note-submit"));

    expect(addNoteMutate).toHaveBeenCalledWith({ text: "Looking good." });
  });

  it("Activity tab shows every audit row including non-note actions", () => {
    renderPage(
      makeDetail({
        recent_audit: [
          {
            id: 10,
            tenant_id: "tn",
            actor_user_id: "u1",
            action: "case.closed",
            target_type: "case",
            target_id: "case-1",
            payload: { reason: "done" },
            occurred_at: "2026-06-12T18:00:00Z",
          },
          {
            id: 9,
            tenant_id: "tn",
            actor_user_id: "u1",
            action: "case.clip_attached",
            target_type: "case",
            target_id: "case-1",
            payload: { clip_id: "x" },
            occurred_at: "2026-06-12T17:00:00Z",
          },
        ],
      }),
    );
    fireEvent.click(screen.getByTestId("case-detail-tab-activity"));
    expect(screen.getByTestId("case-detail-activity-10")).toHaveTextContent(
      "case.closed",
    );
    expect(screen.getByTestId("case-detail-activity-9")).toHaveTextContent(
      "case.clip_attached",
    );
  });

  it("Attach clip flow opens modal and forwards chosen id to useAttachClip", () => {
    renderPage();
    fireEvent.click(screen.getByTestId("case-detail-attach"));
    expect(screen.getByTestId("attach-clip-modal")).toBeInTheDocument();

    // Pick a truck so the mocked clips render (the modal gates on a filter).
    fireEvent.change(screen.getByTestId("attach-clip-truck"), {
      target: { value: "t1" },
    });

    fireEvent.click(screen.getByTestId("attach-clip-add-clip-99"));

    expect(attachMutate).toHaveBeenCalled();
    const [body] = attachMutate.mock.calls[0] ?? [];
    expect((body as { clip_id: string }).clip_id).toBe("clip-99");
  });

  it("renders closed badge and disables actions when case is closed", () => {
    renderPage(makeDetail({ status: "closed" }));
    expect(screen.getByTestId("case-status-closed")).toBeInTheDocument();
    expect(screen.getByTestId("case-detail-close")).toBeDisabled();
    expect(screen.getByTestId("case-detail-attach")).toBeDisabled();
    expect(screen.getByTestId("case-detail-status-select")).toBeDisabled();
  });

  it("renders evidence with attached clips and an Open link", () => {
    renderPage(
      makeDetail({
        clips: [
          {
            clip_id: "clip-aaa",
            attached_at: "2026-06-12T18:00:00Z",
            attached_by: "u1",
            note: "good frame",
            truck_label: "Truck-7",
            started_at: "2026-06-12T14:00:00Z",
          },
        ],
      }),
    );
    expect(screen.getByTestId("case-detail-clip-clip-aaa")).toHaveTextContent(
      "Truck-7",
    );
    const openLink = screen.getByTestId("case-detail-clip-open-clip-aaa");
    expect(openLink).toHaveAttribute("href", "/clips/clip-aaa");
  });
});
