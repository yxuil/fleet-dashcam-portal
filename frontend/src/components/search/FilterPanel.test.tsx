import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EMPTY_FILTERS } from "@/hooks/useClips";
import type { DriverOut, TruckOut } from "@/lib/types";

import { FilterPanel } from "./FilterPanel";

const TRUCKS: TruckOut[] = [
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
];

const DRIVERS: DriverOut[] = [
  { id: "d1", tenant_id: "tn", name: "Alice", employee_ref: null },
  { id: "d2", tenant_id: "tn", name: "Bob", employee_ref: null },
];

describe("FilterPanel", () => {
  it("calls onChange with toggled truckIds", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <FilterPanel
        filters={EMPTY_FILTERS}
        onChange={onChange}
        trucks={TRUCKS}
        drivers={DRIVERS}
      />,
    );

    await user.click(screen.getByTestId("filter-trucks-t1"));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ truckIds: ["t1"] }),
    );
  });

  it("calls onChange with text on every keystroke", () => {
    const onChange = vi.fn();
    render(
      <FilterPanel
        filters={EMPTY_FILTERS}
        onChange={onChange}
        trucks={TRUCKS}
        drivers={DRIVERS}
      />,
    );

    const input = screen.getByTestId("filter-text");
    fireEvent.change(input, { target: { value: "ab" } });
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ text: "ab" }),
    );
  });

  it("toggles a driver off when clicked twice", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <FilterPanel
        filters={EMPTY_FILTERS}
        onChange={onChange}
        trucks={TRUCKS}
        drivers={DRIVERS}
      />,
    );
    await user.click(screen.getByTestId("filter-drivers-d1"));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ driverIds: ["d1"] }),
    );

    // Simulate the parent committing the new filter back in.
    rerender(
      <FilterPanel
        filters={{ ...EMPTY_FILTERS, driverIds: ["d1"] }}
        onChange={onChange}
        trucks={TRUCKS}
        drivers={DRIVERS}
      />,
    );
    await user.click(screen.getByTestId("filter-drivers-d1"));
    expect(onChange).toHaveBeenLastCalledWith(
      expect.objectContaining({ driverIds: [] }),
    );
  });

  it("renders disabled event-type chips with a v2 tooltip", () => {
    render(
      <FilterPanel
        filters={EMPTY_FILTERS}
        onChange={() => {}}
        trucks={TRUCKS}
        drivers={DRIVERS}
      />,
    );

    const chip = screen.getByRole("button", { name: /harsh brake/i });
    expect(chip).toBeDisabled();
    expect(chip).toHaveAttribute("title", "Coming in v2");
  });

  it("shows Clear all only when filters are set, and resets to empty when clicked", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();

    const { rerender } = render(
      <FilterPanel
        filters={EMPTY_FILTERS}
        onChange={onChange}
        trucks={TRUCKS}
        drivers={DRIVERS}
      />,
    );
    expect(screen.queryByTestId("clear-filters")).not.toBeInTheDocument();

    rerender(
      <FilterPanel
        filters={{ ...EMPTY_FILTERS, text: "abc" }}
        onChange={onChange}
        trucks={TRUCKS}
        drivers={DRIVERS}
      />,
    );
    await user.click(screen.getByTestId("clear-filters"));
    expect(onChange).toHaveBeenLastCalledWith(EMPTY_FILTERS);
  });
});
