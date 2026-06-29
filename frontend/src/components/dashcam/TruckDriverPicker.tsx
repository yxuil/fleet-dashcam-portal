/**
 * Single-popover Truck / Driver picker for Fleet Cam.
 *
 * One trigger button summarises the current selection. The popover
 * stacks two sections — Truck on top, Driver below — each with its own
 * search box. Click a row to select; click the highlighted row again to
 * deselect. Apply commits; Clear resets both.
 *
 * Deliberately not built on Radix — the rest of the app's popovers also
 * use hand-rolled click-outside listeners.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import type { DriverOut, TruckOut } from "@/lib/types";
import { cn } from "@/lib/utils";

export type TruckDriverPickerValue = {
  truck_id?: string;
  driver_id?: string;
};

export type TruckDriverPickerProps = {
  trucks: readonly TruckOut[];
  drivers: readonly DriverOut[];
  value: TruckDriverPickerValue;
  onChange: (next: TruckDriverPickerValue) => void;
};

function truckLabel(trucks: readonly TruckOut[], id: string | undefined): string | null {
  if (!id) return null;
  return trucks.find((t) => t.id === id)?.label ?? null;
}

function driverLabel(drivers: readonly DriverOut[], id: string | undefined): string | null {
  if (!id) return null;
  return drivers.find((d) => d.id === id)?.name ?? null;
}

export function TruckDriverPicker({
  trucks,
  drivers,
  value,
  onChange,
}: TruckDriverPickerProps) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  // The popover edits a local "draft" copy; Apply commits it to the
  // parent, Clear resets it. This avoids re-rendering the rest of the
  // page on every keystroke in the search boxes.
  const [draft, setDraft] = useState<TruckDriverPickerValue>(value);
  const [truckQuery, setTruckQuery] = useState("");
  const [driverQuery, setDriverQuery] = useState("");

  // Reset the draft whenever the popover re-opens so dismissing without
  // Apply discards in-progress edits.
  useEffect(() => {
    if (open) {
      setDraft(value);
      setTruckQuery("");
      setDriverQuery("");
    }
  }, [open, value]);

  // Click-outside + Escape close the popover.
  useEffect(() => {
    if (!open) return;
    function onPointer(e: PointerEvent) {
      if (!wrapperRef.current) return;
      if (!wrapperRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const filteredTrucks = useMemo(() => {
    const q = truckQuery.trim().toLowerCase();
    if (!q) return trucks;
    return trucks.filter((t) => t.label.toLowerCase().includes(q));
  }, [trucks, truckQuery]);

  const filteredDrivers = useMemo(() => {
    const q = driverQuery.trim().toLowerCase();
    if (!q) return drivers;
    return drivers.filter((d) => d.name.toLowerCase().includes(q));
  }, [drivers, driverQuery]);

  const summary = useMemo(() => {
    const tl = truckLabel(trucks, value.truck_id);
    const dl = driverLabel(drivers, value.driver_id);
    if (tl && dl) return `${tl} · ${dl}`;
    if (tl) return tl;
    if (dl) return dl;
    return "All trucks & drivers";
  }, [trucks, drivers, value]);

  function apply() {
    onChange(draft);
    setOpen(false);
  }
  function clear() {
    setDraft({});
    onChange({});
    setOpen(false);
  }

  return (
    <div
      ref={wrapperRef}
      className="relative inline-block"
      data-testid="truck-driver-picker"
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        data-testid="truck-driver-picker-trigger"
        aria-label="Truck or driver"
        className={cn(
          "inline-flex h-9 items-center gap-2 rounded-md border border-input bg-background px-3 text-sm",
          "hover:bg-accent",
          open && "bg-accent",
        )}
      >
        <span>{summary}</span>
        <span aria-hidden className="text-xs">
          ▾
        </span>
      </button>
      {open ? (
        <div
          role="dialog"
          data-testid="truck-driver-picker-popover"
          className="absolute z-40 mt-2 w-80 rounded-md border border-border bg-background p-3 shadow-lg"
        >
          <PickerSection
            heading="Truck"
            query={truckQuery}
            onQueryChange={setTruckQuery}
            items={filteredTrucks.map((t) => ({ id: t.id, label: t.label }))}
            selectedId={draft.truck_id}
            onSelect={(id) =>
              setDraft((d) => ({
                ...d,
                truck_id: d.truck_id === id ? undefined : id,
              }))
            }
            testidPrefix="truck"
          />
          <div className="my-3 h-px bg-border" />
          <PickerSection
            heading="Driver"
            query={driverQuery}
            onQueryChange={setDriverQuery}
            items={filteredDrivers.map((d) => ({ id: d.id, label: d.name }))}
            selectedId={draft.driver_id}
            onSelect={(id) =>
              setDraft((d) => ({
                ...d,
                driver_id: d.driver_id === id ? undefined : id,
              }))
            }
            testidPrefix="driver"
          />
          <div className="mt-3 flex items-center justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={clear}
              data-testid="truck-driver-picker-clear"
            >
              Clear
            </Button>
            <Button
              size="sm"
              onClick={apply}
              data-testid="truck-driver-picker-apply"
            >
              Apply
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

type PickerSectionProps = {
  heading: string;
  query: string;
  onQueryChange: (q: string) => void;
  items: { id: string; label: string }[];
  selectedId: string | undefined;
  onSelect: (id: string) => void;
  testidPrefix: string;
};

function PickerSection({
  heading,
  query,
  onQueryChange,
  items,
  selectedId,
  onSelect,
  testidPrefix,
}: PickerSectionProps) {
  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {heading}
        </span>
      </div>
      <div className="relative">
        <input
          type="text"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder={`Search ${heading.toLowerCase()}`}
          data-testid={`truck-driver-picker-${testidPrefix}-search`}
          className="mb-2 h-8 w-full rounded-md border border-input bg-background px-2 text-sm"
        />
      </div>
      <ul
        className="max-h-40 overflow-y-auto rounded-md border border-border"
        data-testid={`truck-driver-picker-${testidPrefix}-list`}
      >
        {items.length === 0 ? (
          <li className="px-2 py-1.5 text-xs text-muted-foreground">
            None match.
          </li>
        ) : (
          items.map((item) => {
            const selected = item.id === selectedId;
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => onSelect(item.id)}
                  data-testid={`truck-driver-picker-${testidPrefix}-${item.id}`}
                  aria-pressed={selected}
                  className={cn(
                    "block w-full px-2 py-1.5 text-left text-sm",
                    selected
                      ? "bg-primary text-primary-foreground"
                      : "hover:bg-accent",
                  )}
                >
                  {item.label}
                </button>
              </li>
            );
          })
        )}
      </ul>
    </div>
  );
}
