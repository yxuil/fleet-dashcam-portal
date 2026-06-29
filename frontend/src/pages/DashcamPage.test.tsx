import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TruckDay } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mocks — stub the data hooks so the page renders in isolation.
// ---------------------------------------------------------------------------

const sampleDays: TruckDay[] = [
  {
    date: "2026-06-28",
    clip_count: 2,
    first_clip_id: "11111111-1111-1111-1111-111111111111",
    total_duration_s: 150,
  },
  {
    date: "2026-06-27",
    clip_count: 1,
    first_clip_id: "22222222-2222-2222-2222-222222222222",
    total_duration_s: 90,
  },
];

const updatePrefsMock = vi.fn();

vi.mock("@/hooks/useTrucks", () => ({
  useTrucks: () => ({
    data: [
      {
        id: "t1",
        tenant_id: "tn",
        label: "Truck 101",
        vin: null,
        dashcam_serial: null,
        last_seen_at: null,
      },
      {
        id: "t2",
        tenant_id: "tn",
        label: "Truck 102",
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

vi.mock("@/hooks/usePrefs", () => ({
  PREFS_QUERY_KEY: ["me-prefs"] as const,
  usePrefs: () => ({ data: {}, isLoading: false }),
}));

vi.mock("@/hooks/useUpdatePrefs", () => ({
  useUpdatePrefs: () => ({ mutate: updatePrefsMock, isPending: false }),
}));

vi.mock("@/hooks/useTruckDays", () => ({
  useTruckDays: () => ({
    data: sampleDays,
    isLoading: false,
    isError: false,
  }),
}));

import { DashcamPage } from "./DashcamPage";

function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="location-search">{loc.search}</span>;
}

function renderPage(initial = "/dashcam") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <LocationProbe />
        <Routes>
          <Route path="/dashcam" element={<DashcamPage />} />
          <Route path="/clips/:id" element={<div>clip detail</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("DashcamPage", () => {
  beforeEach(() => {
    updatePrefsMock.mockReset();
  });

  afterEach(() => {
    updatePrefsMock.mockReset();
  });

  it("renders the Fleet Cam title and a row per truck", () => {
    renderPage();
    expect(
      screen.getByRole("heading", { name: "Fleet Cam" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("truck-row-t1")).toBeInTheDocument();
    expect(screen.getByTestId("truck-row-t2")).toBeInTheDocument();
  });

  it("hydrates the truck filter from the URL", () => {
    renderPage("/dashcam?truck_id=t1");
    // Only the filtered truck row is visible.
    expect(screen.getByTestId("truck-row-t1")).toBeInTheDocument();
    expect(screen.queryByTestId("truck-row-t2")).not.toBeInTheDocument();
  });

  it("commits picker selection to the URL via Apply", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByTestId("truck-driver-picker-trigger"));
    await user.click(screen.getByTestId("truck-driver-picker-truck-t2"));
    await user.click(screen.getByTestId("truck-driver-picker-apply"));

    await waitFor(() => {
      expect(screen.getByTestId("location-search").textContent).toContain(
        "truck_id=t2",
      );
    });
    expect(screen.getByTestId("truck-row-t2")).toBeInTheDocument();
    expect(screen.queryByTestId("truck-row-t1")).not.toBeInTheDocument();
  });

  it("navigates to /clips/<first_clip_id> when a day card is clicked", async () => {
    const user = userEvent.setup();
    renderPage();
    // Picks the first card in the first truck row.
    const cards = screen.getAllByTestId(`day-card-${sampleDays[0].date}`);
    await user.click(cards[0]);
    expect(screen.getByText("clip detail")).toBeInTheDocument();
  });

  it("invokes useUpdatePrefs.mutate when the row's down arrow is clicked", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("truck-row-t1-down"));
    expect(updatePrefsMock).toHaveBeenCalledTimes(1);
    expect(updatePrefsMock).toHaveBeenCalledWith({
      truck_order: ["t2", "t1"],
    });
  });
});
