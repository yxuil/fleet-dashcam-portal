/**
 * Tests for `<CaseListPage />`.
 *
 * Strategy: mock the data hooks (`useCasesList`, `useMe`) so the page
 * is driven entirely by what its dependencies return; render inside a
 * `MemoryRouter` so card navigation works.
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

import type { CaseFilters } from "@/hooks/useCasesList";
import type { CaseRow } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const useCasesListCalls: CaseFilters[] = [];
const fetchNextPage = vi.fn();
const refetch = vi.fn();

const sampleCases: CaseRow[] = [
  {
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
  },
  {
    id: "case-2",
    tenant_id: "tn",
    number: "C-2026-0002",
    external_ref: null,
    requester_name: "Bob",
    requester_org: null,
    incident_at: null,
    status: "approved",
    assignee_user_id: "u2",
    due_at: null,
    created_by: "u1",
    created_at: "2026-06-13T09:00:00Z",
  },
];

vi.mock("@/hooks/useCasesList", async () => {
  const actual = await vi.importActual<typeof import("@/hooks/useCasesList")>(
    "@/hooks/useCasesList",
  );
  return {
    ...actual,
    useCasesList: (filters: CaseFilters) => {
      useCasesListCalls.push(filters);
      return {
        data: {
          items: sampleCases,
          pages: [{ items: sampleCases, next_cursor: null }],
          pageParams: [null],
        },
        isLoading: false,
        isFetching: false,
        isFetchingNextPage: false,
        isError: false,
        error: null,
        fetchNextPage,
        refetch,
      };
    },
  };
});

vi.mock("@/hooks/useMe", () => ({
  useMe: () => ({
    data: {
      user_id: "me-user-id",
      tenant_id: "tn",
      name: "Me",
      email: "me@example.com",
      roles: ["viewer"],
    },
    isLoading: false,
    isError: false,
  }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

import { CaseListPage } from "./CaseListPage";

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/cases"]}>
        <Routes>
          <Route path="/cases" element={<CaseListPage />} />
          <Route
            path="/cases/:id"
            element={<div data-testid="case-detail-page">case detail</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("CaseListPage", () => {
  beforeEach(() => {
    useCasesListCalls.length = 0;
    fetchNextPage.mockReset();
    refetch.mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders a card for each case with number, requester, status", () => {
    renderPage();
    expect(screen.getByTestId("cases-card-case-1")).toBeInTheDocument();
    expect(screen.getByTestId("cases-card-case-2")).toBeInTheDocument();
    expect(screen.getByTestId("cases-card-case-1")).toHaveTextContent(
      "C-2026-0001",
    );
    expect(screen.getByTestId("cases-card-requester-case-1")).toHaveTextContent(
      "Alice",
    );
    // Status badge is part of the card.
    expect(
      screen.getByTestId("cases-card-case-1").querySelector(
        '[data-testid="case-status-open"]',
      ),
    ).not.toBeNull();
    expect(
      screen.getByTestId("cases-card-case-2").querySelector(
        '[data-testid="case-status-approved"]',
      ),
    ).not.toBeNull();
  });

  it("toggles status chip filters and forwards them to useCasesList", () => {
    renderPage();
    // First call should be with empty statuses.
    expect(useCasesListCalls.at(-1)?.statuses).toEqual([]);

    fireEvent.click(screen.getByTestId("cases-status-chip-open"));
    expect(useCasesListCalls.at(-1)?.statuses).toEqual(["open"]);

    fireEvent.click(screen.getByTestId("cases-status-chip-approved"));
    expect(useCasesListCalls.at(-1)?.statuses).toEqual(["open", "approved"]);

    fireEvent.click(screen.getByTestId("cases-status-chip-open"));
    expect(useCasesListCalls.at(-1)?.statuses).toEqual(["approved"]);
  });

  it("'Assigned to me' uses the current user's id as the assignee filter", () => {
    renderPage();
    expect(useCasesListCalls.at(-1)?.assigneeUserId).toBeNull();

    fireEvent.click(screen.getByTestId("cases-assigned-to-me"));
    expect(useCasesListCalls.at(-1)?.assigneeUserId).toBe("me-user-id");
  });

  it("updates `q` filter on text input", () => {
    renderPage();
    fireEvent.change(screen.getByTestId("cases-q"), {
      target: { value: "alice" },
    });
    expect(useCasesListCalls.at(-1)?.q).toBe("alice");
  });

  it("navigates to /cases/:id when a card is clicked", async () => {
    renderPage();
    fireEvent.click(screen.getByTestId("cases-card-case-1"));
    await waitFor(() => {
      expect(screen.getByTestId("case-detail-page")).toBeInTheDocument();
    });
  });
});
