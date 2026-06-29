import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ClipFilters } from "@/hooks/useClips";
import type { ClipRow } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

// Capture every `useClips(filters)` invocation so we can assert how many
// debounced requests fire when the user types.
const useClipsCalls: ClipFilters[] = [];

const fetchNextPage = vi.fn();
const refetch = vi.fn();

const sampleClips: ClipRow[] = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    tenant_id: "tn",
    truck_id: "t1",
    truck_label: "Truck-1",
    driver_id: "d1",
    driver_name: "Alice",
    started_at: "2026-06-12T14:32:00Z",
    ended_at: "2026-06-12T14:33:00Z",
    duration_s: 83,
    storage_key: "tn/2026/06/12/x.mp4",
    ingested_at: "2026-06-12T14:34:00Z",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    tenant_id: "tn",
    truck_id: "t2",
    truck_label: "Truck-2",
    driver_id: null,
    driver_name: null,
    started_at: "2026-06-12T15:00:00Z",
    ended_at: "2026-06-12T15:00:30Z",
    duration_s: 30,
    storage_key: "tn/2026/06/12/y.mp4",
    ingested_at: "2026-06-12T15:01:00Z",
  },
];

vi.mock("@/hooks/useClips", async () => {
  const actual = await vi.importActual<typeof import("@/hooks/useClips")>(
    "@/hooks/useClips",
  );
  return {
    ...actual,
    useClips: (filters: ClipFilters) => {
      useClipsCalls.push(filters);
      return {
        data: {
          items: sampleClips,
          pages: [{ items: sampleClips, next_cursor: null }],
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

vi.mock("@/hooks/useTrucks", () => ({
  useTrucks: () => ({
    data: [
      {
        id: "t1",
        tenant_id: "tn",
        label: "Truck-1",
        vin: null,
        dashcam_serial: null,
        last_seen_at: null,
      },
      {
        id: "t2",
        tenant_id: "tn",
        label: "Truck-2",
        vin: null,
        dashcam_serial: null,
        last_seen_at: null,
      },
    ],
    isLoading: false,
  }),
}));

vi.mock("@/hooks/useDrivers", () => ({
  useDrivers: () => ({
    data: [{ id: "d1", tenant_id: "tn", name: "Alice", employee_ref: null }],
    isLoading: false,
  }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

import { SearchPage } from "./SearchPage";

function LocationProbe() {
  const loc = useLocation();
  return (
    <span data-testid="location-search">{loc.search}</span>
  );
}

function renderPage(initialPath = "/search") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialPath]}>
        <LocationProbe />
        <Routes>
          <Route path="/search" element={<SearchPage />} />
          <Route path="/clips/:id" element={<div>clip detail</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SearchPage", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    useClipsCalls.length = 0;
    fetchNextPage.mockReset();
    refetch.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders ClipCards for each result", () => {
    renderPage();
    // Cards exist for each clip in the results grid.
    expect(
      screen.getByTestId(`clip-card-${sampleClips[0].id}`),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId(`clip-card-${sampleClips[1].id}`),
    ).toBeInTheDocument();
    // Truck label and driver name surface on the cards via test ids
    // (label text also appears in the filter sidebar, so use a scoped
    // assertion).
    const labels = screen.getAllByTestId("clip-truck-label");
    expect(labels.map((el) => el.textContent)).toEqual(["Truck-1", "Truck-2"]);
    const drivers = screen.getAllByTestId("clip-driver-name");
    expect(drivers.map((el) => el.textContent)).toEqual(["Alice", "—"]);
  });

  it("hydrates filter state from the URL", () => {
    renderPage("/search?q=foo&truck=t1");
    const input = screen.getByTestId("filter-text") as HTMLInputElement;
    expect(input.value).toBe("foo");
    const t1Checkbox = screen.getByTestId(
      "filter-trucks-t1",
    ) as HTMLInputElement;
    expect(t1Checkbox.checked).toBe(true);
  });

  it("updates the URL when the text filter changes", async () => {
    const user = userEvent.setup({
      advanceTimers: vi.advanceTimersByTime.bind(vi),
    });
    renderPage();

    await user.type(screen.getByTestId("filter-text"), "abc");

    // URL reflects current filters immediately.
    expect(screen.getByTestId("location-search").textContent).toContain(
      "q=abc",
    );
  });

  it("debounces useClips to one stable call after typing settles", async () => {
    const user = userEvent.setup({
      advanceTimers: vi.advanceTimersByTime.bind(vi),
    });
    renderPage();

    // Initial render fires one call with empty filters.
    expect(useClipsCalls.length).toBeGreaterThanOrEqual(1);
    const baseline = useClipsCalls.length;

    await user.type(screen.getByTestId("filter-text"), "abc");

    // Before the debounce window elapses, useClips still sees the
    // *previous* (debounced) value — text stays empty.
    expect(
      useClipsCalls.slice(baseline).every((f) => f.text === ""),
    ).toBe(true);

    // Flush the 300ms debounce window.
    await act(async () => {
      vi.advanceTimersByTime(350);
    });

    await waitFor(() => {
      expect(useClipsCalls.at(-1)?.text).toBe("abc");
    });
  });

  it("navigates to /clips/:id when a card is clicked", async () => {
    const user = userEvent.setup({
      advanceTimers: vi.advanceTimersByTime.bind(vi),
    });
    renderPage();

    await user.click(screen.getByTestId(`clip-card-${sampleClips[0].id}`));
    expect(screen.getByText("clip detail")).toBeInTheDocument();
  });
});
