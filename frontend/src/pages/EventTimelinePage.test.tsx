/**
 * Tests for `<EventTimelinePage />`.
 *
 * Strategy: mock the React Query hooks and the typed API helpers so the
 * page is driven entirely by what its dependencies return. The page is
 * rendered inside a `MemoryRouter` with a catch-all "/cases/:id" route
 * so we can assert that the open-case flow lands the user on the new
 * case page.
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

import type { EventRow as EventRowData } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mocks — declared before importing the page so the page picks them up.
// ---------------------------------------------------------------------------

const apiPost = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    apiPost: (...args: unknown[]) => apiPost(...args),
  };
});

const useTimelineEventsMock = vi.fn();
vi.mock("@/hooks/useTimelineEvents", () => ({
  useTimelineEvents: (...args: unknown[]) => useTimelineEventsMock(...args),
}));

const createCaseMutateAsync = vi.fn();
const useCreateCaseState = {
  isPending: false,
  error: null as unknown,
  reset: vi.fn(),
};
vi.mock("@/hooks/useCreateCase", () => ({
  useCreateCase: () => ({
    mutateAsync: createCaseMutateAsync,
    isPending: useCreateCaseState.isPending,
    error: useCreateCaseState.error,
    reset: useCreateCaseState.reset,
  }),
}));

vi.mock("@/hooks/useTrucks", () => ({
  useTrucks: () => ({
    data: [
      { id: "t1", tenant_id: "tn", label: "Freightliner-99", vin: null, dashcam_serial: null, last_seen_at: null },
    ],
    isLoading: false,
  }),
}));

vi.mock("@/hooks/useDrivers", () => ({
  useDrivers: () => ({
    data: [
      { id: "d1", tenant_id: "tn", name: "Alice", employee_ref: null },
    ],
    isLoading: false,
  }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

import { EventTimelinePage } from "./EventTimelinePage";

function makeEvent(overrides: Partial<EventRowData> = {}): EventRowData {
  return {
    id: "ev-1",
    tenant_id: "tn",
    truck_id: "t1",
    truck_label: "Freightliner-99",
    driver_id: null,
    driver_name: null,
    clip_id: "clip-1",
    occurred_at: "2026-06-29T12:00:00Z",
    type: "harsh_brake",
    severity: "high",
    telemetry: { speed_kmh: 64, accel_g: 1.2 },
    gps_lat: null,
    gps_lng: null,
    ...overrides,
  };
}

type RenderOpts = { scope?: "truck" | "driver"; path?: string };

function renderPage(opts: RenderOpts = {}) {
  const scope = opts.scope ?? "truck";
  const path =
    opts.path ?? (scope === "truck" ? "/trucks/t1/events" : "/drivers/d1/events");
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/trucks/:id/events"
            element={<EventTimelinePage scope="truck" />}
          />
          <Route
            path="/drivers/:id/events"
            element={<EventTimelinePage scope="driver" />}
          />
          <Route
            path="/cases/:id"
            element={<div data-testid="case-detail-page">case detail</div>}
          />
          <Route
            path="/clips/:id"
            element={<div data-testid="clip-detail-page">clip detail</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// Default mock state — each test can override.
function setEvents(items: EventRowData[], extra: Partial<ReturnType<typeof timelineEventsState>> = {}) {
  useTimelineEventsMock.mockReturnValue(timelineEventsState(items, extra));
}

function timelineEventsState(
  items: EventRowData[],
  extra: Partial<{
    isLoading: boolean;
    isError: boolean;
    error: unknown;
    isFetchingNextPage: boolean;
    fetchNextPage: () => void;
    refetch: () => void;
    pages: { items: EventRowData[]; next_cursor: string | null }[];
  }> = {},
) {
  return {
    data: {
      items,
      pages: extra.pages ?? [{ items, next_cursor: null }],
      pageParams: [null],
    },
    isLoading: extra.isLoading ?? false,
    isError: extra.isError ?? false,
    error: extra.error ?? null,
    isFetchingNextPage: extra.isFetchingNextPage ?? false,
    fetchNextPage: extra.fetchNextPage ?? vi.fn(),
    refetch: extra.refetch ?? vi.fn(),
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("EventTimelinePage", () => {
  beforeEach(() => {
    apiPost.mockReset();
    createCaseMutateAsync.mockReset();
    useTimelineEventsMock.mockReset();
    useCreateCaseState.isPending = false;
    useCreateCaseState.error = null;
    useCreateCaseState.reset.mockReset();
    setEvents([
      makeEvent({ id: "ev-1", severity: "high", type: "harsh_brake" }),
      makeEvent({ id: "ev-2", severity: "critical", type: "collision" }),
    ]);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders rows from useTimelineEvents", () => {
    renderPage();
    expect(screen.getByTestId("event-row-ev-1")).toBeInTheDocument();
    expect(screen.getByTestId("event-row-ev-2")).toBeInTheDocument();
  });

  it("renders the truck label in the header for the truck scope", () => {
    renderPage({ scope: "truck", path: "/trucks/t1/events" });
    expect(screen.getByTestId("timeline-header-label")).toHaveTextContent(
      "Freightliner-99",
    );
  });

  it("renders the driver name in the header for the driver scope", () => {
    renderPage({ scope: "driver", path: "/drivers/d1/events" });
    expect(screen.getByTestId("timeline-header-label")).toHaveTextContent(
      "Alice",
    );
  });

  it("passes filter state to useTimelineEvents when severity tab changes", () => {
    renderPage();
    // Initial "all" call: severities is empty.
    const initialCall = useTimelineEventsMock.mock.calls.at(-1)?.[0];
    expect(initialCall).toMatchObject({
      truckId: "t1",
      driverId: undefined,
      severities: [],
    });

    fireEvent.click(screen.getByTestId("severity-tab-critical"));
    const nextCall = useTimelineEventsMock.mock.calls.at(-1)?.[0];
    expect(nextCall).toMatchObject({
      truckId: "t1",
      severities: ["critical"],
    });
  });

  it("toggles type-chip selection", () => {
    renderPage();
    fireEvent.click(screen.getByTestId("type-chip-harsh_brake"));
    let last = useTimelineEventsMock.mock.calls.at(-1)?.[0];
    expect(last.types).toEqual(["harsh_brake"]);

    fireEvent.click(screen.getByTestId("type-chip-collision"));
    last = useTimelineEventsMock.mock.calls.at(-1)?.[0];
    expect(last.types).toEqual(["harsh_brake", "collision"]);

    // Toggling the same chip again removes it.
    fireEvent.click(screen.getByTestId("type-chip-harsh_brake"));
    last = useTimelineEventsMock.mock.calls.at(-1)?.[0];
    expect(last.types).toEqual(["collision"]);
  });

  it("scopes by driver_id when scope='driver'", () => {
    renderPage({ scope: "driver", path: "/drivers/d1/events" });
    const call = useTimelineEventsMock.mock.calls.at(-1)?.[0];
    expect(call).toMatchObject({
      truckId: undefined,
      driverId: "d1",
    });
  });

  it("calls POST /events/:id/triage and shows an inline badge", async () => {
    apiPost.mockResolvedValueOnce({});
    renderPage();

    fireEvent.click(screen.getByTestId("event-triage-trigger-ev-1"));
    fireEvent.click(screen.getByTestId("event-triage-coach-ev-1"));

    // Optimistic badge appears immediately.
    expect(screen.getByTestId("event-triaged-ev-1")).toHaveTextContent(
      "Coaching note",
    );

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith("/events/ev-1/triage", {
        label: "coaching_note",
      });
    });
  });

  it("reverts the inline badge if the triage POST fails", async () => {
    apiPost.mockRejectedValueOnce(new Error("boom"));
    renderPage();

    fireEvent.click(screen.getByTestId("event-triage-trigger-ev-1"));
    fireEvent.click(screen.getByTestId("event-triage-fp-ev-1"));

    // Optimistic appearance, then rollback after the mutation rejects.
    expect(screen.getByTestId("event-triaged-ev-1")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId("event-triaged-ev-1")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("triage-error")).toBeInTheDocument();
  });

  it("opens the case modal, submits, and navigates to /cases/:newId", async () => {
    createCaseMutateAsync.mockResolvedValueOnce({
      id: "case-77",
      number: "C-2026-0077",
      tenant_id: "tn",
    });
    apiPost.mockResolvedValue({}); // for the open_case triage audit

    renderPage();

    fireEvent.click(screen.getByTestId("event-triage-trigger-ev-1"));
    fireEvent.click(screen.getByTestId("event-triage-open-case-ev-1"));

    // Modal visible.
    expect(screen.getByTestId("open-case-modal")).toBeInTheDocument();

    // Fill in the required field, submit.
    fireEvent.change(screen.getByTestId("open-case-requester-name"), {
      target: { value: "Captain Risk" },
    });
    fireEvent.click(screen.getByTestId("open-case-submit"));

    await waitFor(() => {
      expect(createCaseMutateAsync).toHaveBeenCalled();
    });
    const call = createCaseMutateAsync.mock.calls[0]?.[0];
    expect(call.clipId).toBe("clip-1");
    expect(call.case.requester_name).toBe("Captain Risk");

    // Navigation happened.
    await waitFor(() => {
      expect(screen.getByTestId("case-detail-page")).toBeInTheDocument();
    });

    // Best-effort triage audit fires.
    await waitFor(() => {
      const calls = apiPost.mock.calls.map((c) => c[0] as string);
      expect(calls).toContain("/events/ev-1/triage");
    });
  });

  it("keeps the modal open and surfaces error if case creation fails", async () => {
    createCaseMutateAsync.mockRejectedValueOnce(new Error("nope"));
    useCreateCaseState.error = new Error("nope");

    renderPage();
    fireEvent.click(screen.getByTestId("event-triage-trigger-ev-1"));
    fireEvent.click(screen.getByTestId("event-triage-open-case-ev-1"));

    fireEvent.change(screen.getByTestId("open-case-requester-name"), {
      target: { value: "Captain Risk" },
    });
    fireEvent.click(screen.getByTestId("open-case-submit"));

    await waitFor(() => {
      expect(createCaseMutateAsync).toHaveBeenCalled();
    });
    expect(screen.getByTestId("open-case-modal")).toBeInTheDocument();
    expect(screen.getByTestId("open-case-error")).toBeInTheDocument();
  });

  it("hides the open-case menu item when the event has no clip", () => {
    setEvents([makeEvent({ id: "ev-1", clip_id: null })]);
    renderPage();

    fireEvent.click(screen.getByTestId("event-triage-trigger-ev-1"));
    expect(
      screen.getByTestId("event-triage-open-case-ev-1"),
    ).toBeDisabled();
    expect(screen.getByTestId("event-open-clip-ev-1")).toBeDisabled();
  });

  it("renders an empty state when there are no events", () => {
    setEvents([]);
    renderPage();
    expect(screen.getByTestId("timeline-empty")).toBeInTheDocument();
  });

  it("renders an error state and retries on click", () => {
    const refetch = vi.fn();
    useTimelineEventsMock.mockReturnValue(
      timelineEventsState([], { isError: true, refetch, error: new Error("x") }),
    );
    renderPage();

    expect(screen.getByTestId("timeline-error")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry"));
    expect(refetch).toHaveBeenCalled();
  });
});
