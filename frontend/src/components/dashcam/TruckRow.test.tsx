import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { TruckDay, TruckOut } from "@/lib/types";

// Mock the truck-days hook before importing the SUT.
vi.mock("@/hooks/useTruckDays", () => ({
  useTruckDays: () => ({
    data: [
      {
        date: "2026-06-28",
        clip_count: 1,
        first_clip_id: "11111111-1111-1111-1111-111111111111",
        total_duration_s: 90,
      },
      {
        date: "2026-06-27",
        clip_count: 2,
        first_clip_id: "22222222-2222-2222-2222-222222222222",
        total_duration_s: 120,
      },
    ] satisfies TruckDay[],
    isLoading: false,
    isError: false,
  }),
}));

import { TruckRow } from "./TruckRow";

const truck: TruckOut = {
  id: "t1",
  tenant_id: "tn",
  label: "Truck 101",
  vin: null,
  dashcam_serial: null,
  last_seen_at: null,
};

function renderRow(opts: { index: number; total: number; onReorder?: () => void }) {
  const onReorder = opts.onReorder ?? vi.fn();
  return {
    onReorder,
    ...render(
      <MemoryRouter>
        <TruckRow
          truck={truck}
          filters={{}}
          position={{ index: opts.index, total: opts.total }}
          onReorder={onReorder}
        />
      </MemoryRouter>,
    ),
  };
}

describe("TruckRow", () => {
  it("renders one day card per returned day in the hook order", () => {
    renderRow({ index: 0, total: 2 });
    const cards = screen.getAllByRole("button", { name: /./ });
    // The day cards have testids like `day-card-2026-06-28`.
    expect(screen.getByTestId("day-card-2026-06-28")).toBeInTheDocument();
    expect(screen.getByTestId("day-card-2026-06-27")).toBeInTheDocument();
    // Cards is at least the two day cards + two arrow buttons.
    expect(cards.length).toBeGreaterThanOrEqual(4);
  });

  it("disables the up arrow when the row is first", () => {
    renderRow({ index: 0, total: 3 });
    expect(screen.getByTestId("truck-row-t1-up")).toBeDisabled();
    expect(screen.getByTestId("truck-row-t1-down")).not.toBeDisabled();
  });

  it("disables the down arrow when the row is last", () => {
    renderRow({ index: 2, total: 3 });
    expect(screen.getByTestId("truck-row-t1-up")).not.toBeDisabled();
    expect(screen.getByTestId("truck-row-t1-down")).toBeDisabled();
  });

  it("invokes onReorder with 'up' / 'down' when arrows are clicked", async () => {
    const user = userEvent.setup();
    const onReorder = vi.fn();
    render(
      <MemoryRouter>
        <TruckRow
          truck={truck}
          filters={{}}
          position={{ index: 1, total: 3 }}
          onReorder={onReorder}
        />
      </MemoryRouter>,
    );
    await user.click(screen.getByTestId("truck-row-t1-up"));
    await user.click(screen.getByTestId("truck-row-t1-down"));
    expect(onReorder).toHaveBeenCalledTimes(2);
    expect(onReorder.mock.calls[0]?.[0]).toBe("up");
    expect(onReorder.mock.calls[1]?.[0]).toBe("down");
  });
});
