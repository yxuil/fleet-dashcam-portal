import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { DriverOut, TruckOut } from "@/lib/types";

import { TruckDriverPicker } from "./TruckDriverPicker";

const trucks: TruckOut[] = [
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
    label: "Truck 202",
    vin: null,
    dashcam_serial: null,
    last_seen_at: null,
  },
];

const drivers: DriverOut[] = [
  { id: "d1", tenant_id: "tn", name: "Alice", employee_ref: null },
  { id: "d2", tenant_id: "tn", name: "Bob", employee_ref: null },
];

describe("TruckDriverPicker", () => {
  it("opens a popover with both Truck and Driver sections", async () => {
    const user = userEvent.setup();
    render(
      <TruckDriverPicker
        trucks={trucks}
        drivers={drivers}
        value={{}}
        onChange={() => {}}
      />,
    );
    await user.click(screen.getByTestId("truck-driver-picker-trigger"));
    expect(
      screen.getByTestId("truck-driver-picker-popover"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("truck-driver-picker-truck-list"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("truck-driver-picker-driver-list"),
    ).toBeInTheDocument();
  });

  it("filters the truck list by the search box", async () => {
    const user = userEvent.setup();
    render(
      <TruckDriverPicker
        trucks={trucks}
        drivers={drivers}
        value={{}}
        onChange={() => {}}
      />,
    );
    await user.click(screen.getByTestId("truck-driver-picker-trigger"));
    expect(screen.getByTestId("truck-driver-picker-truck-t1")).toBeVisible();
    expect(screen.getByTestId("truck-driver-picker-truck-t2")).toBeVisible();

    await user.type(
      screen.getByTestId("truck-driver-picker-truck-search"),
      "101",
    );
    expect(
      screen.queryByTestId("truck-driver-picker-truck-t2"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("truck-driver-picker-truck-t1")).toBeVisible();
  });

  it("clicking the selected truck deselects it", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <TruckDriverPicker
        trucks={trucks}
        drivers={drivers}
        value={{}}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByTestId("truck-driver-picker-trigger"));
    // Select t1.
    await user.click(screen.getByTestId("truck-driver-picker-truck-t1"));
    expect(
      screen.getByTestId("truck-driver-picker-truck-t1"),
    ).toHaveAttribute("aria-pressed", "true");

    // Click again to deselect.
    await user.click(screen.getByTestId("truck-driver-picker-truck-t1"));
    expect(
      screen.getByTestId("truck-driver-picker-truck-t1"),
    ).toHaveAttribute("aria-pressed", "false");

    // Apply with no selection commits an empty object.
    await user.click(screen.getByTestId("truck-driver-picker-apply"));
    expect(onChange).toHaveBeenCalledWith({
      truck_id: undefined,
      driver_id: undefined,
    });
  });

  it("Apply forwards the chosen truck + driver to onChange", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <TruckDriverPicker
        trucks={trucks}
        drivers={drivers}
        value={{}}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByTestId("truck-driver-picker-trigger"));
    await user.click(screen.getByTestId("truck-driver-picker-truck-t2"));
    await user.click(screen.getByTestId("truck-driver-picker-driver-d1"));
    await user.click(screen.getByTestId("truck-driver-picker-apply"));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({
      truck_id: "t2",
      driver_id: "d1",
    });
  });

  it("Clear resets both selections", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <TruckDriverPicker
        trucks={trucks}
        drivers={drivers}
        value={{ truck_id: "t1", driver_id: "d2" }}
        onChange={onChange}
      />,
    );

    await user.click(screen.getByTestId("truck-driver-picker-trigger"));
    await user.click(screen.getByTestId("truck-driver-picker-clear"));
    expect(onChange).toHaveBeenCalledWith({});
  });
});
