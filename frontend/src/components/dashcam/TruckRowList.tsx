/**
 * The list of Fleet Cam truck rows.
 *
 * Resolves display order from `prefs.truck_order`, falling back to
 * alphabetical label order for any truck not yet ordered. The reorder
 * arrows on each row update the server-side prefs via
 * `useUpdatePrefs()`, which optimistically writes the new order into
 * the React Query cache so the swap is instant.
 */

import { useMemo } from "react";

import { usePrefs } from "@/hooks/usePrefs";
import { useTrucks } from "@/hooks/useTrucks";
import type { TruckDaysFilters } from "@/hooks/useTruckDays";
import { useUpdatePrefs } from "@/hooks/useUpdatePrefs";
import type { TruckOut } from "@/lib/types";

import { TruckRow } from "./TruckRow";

export type TruckRowListProps = {
  filters: TruckDaysFilters;
  /** Optional client-side filter to keep only specific truck ids. */
  truckIds?: string[];
  /** Free-text query against truck label, case-insensitive substring. */
  textQuery?: string;
  /**
   * When true, rows that come back from the days endpoint with no
   * results are hidden. Used when a driver filter is active so trucks
   * that driver never drove disappear from the list.
   */
  hideEmptyRows?: boolean;
};

function reorderedTrucks(
  trucks: readonly TruckOut[],
  order: readonly string[] | undefined,
): TruckOut[] {
  if (!order || order.length === 0) {
    return [...trucks].sort((a, b) => a.label.localeCompare(b.label));
  }
  const byId = new Map(trucks.map((t) => [t.id, t]));
  const ordered: TruckOut[] = [];
  for (const id of order) {
    const t = byId.get(id);
    if (t) {
      ordered.push(t);
      byId.delete(id);
    }
  }
  // Anything not yet pinned goes after, alphabetical.
  const remaining = [...byId.values()].sort((a, b) =>
    a.label.localeCompare(b.label),
  );
  return [...ordered, ...remaining];
}

export function TruckRowList({
  filters,
  truckIds,
  textQuery,
  hideEmptyRows,
}: TruckRowListProps) {
  const trucks = useTrucks();
  const prefs = usePrefs();
  const updatePrefs = useUpdatePrefs();

  const ordered = useMemo(
    () => reorderedTrucks(trucks.data ?? [], prefs.data?.truck_order),
    [trucks.data, prefs.data?.truck_order],
  );

  const filtered = useMemo(() => {
    let out = ordered;
    if (truckIds && truckIds.length > 0) {
      const set = new Set(truckIds);
      out = out.filter((t) => set.has(t.id));
    }
    if (textQuery && textQuery.trim()) {
      const q = textQuery.trim().toLowerCase();
      out = out.filter((t) => t.label.toLowerCase().includes(q));
    }
    return out;
  }, [ordered, truckIds, textQuery]);

  if (trucks.isLoading || prefs.isLoading) {
    return (
      <div className="space-y-3" data-testid="truck-row-list-loading">
        {Array.from({ length: 3 }).map((_, i) => (
          <div
            key={i}
            className="h-36 animate-pulse rounded-lg border border-border bg-card"
          />
        ))}
      </div>
    );
  }

  if (filtered.length === 0) {
    return (
      <p
        className="rounded-md border border-dashed border-border p-8 text-center text-sm text-muted-foreground"
        data-testid="truck-row-list-empty"
      >
        No trucks match the current filters.
      </p>
    );
  }

  function reorder(truckId: string, direction: "up" | "down") {
    const ids = filtered.map((t) => t.id);
    const idx = ids.indexOf(truckId);
    if (idx < 0) return;
    const swap = direction === "up" ? idx - 1 : idx + 1;
    if (swap < 0 || swap >= ids.length) return;
    // Build the full order: keep filtered trucks reordered, append the
    // rest of the trucks in their current visual order so we don't
    // accidentally drop unselected trucks from the persisted prefs.
    const newFiltered = [...ids];
    const moved = newFiltered.splice(idx, 1)[0]!;
    newFiltered.splice(swap, 0, moved);
    const filteredSet = new Set(filtered.map((t) => t.id));
    const rest = ordered.filter((t) => !filteredSet.has(t.id)).map((t) => t.id);
    updatePrefs.mutate({ truck_order: [...newFiltered, ...rest] });
  }

  return (
    <div className="space-y-3" data-testid="truck-row-list">
      {filtered.map((truck, idx) => (
        <TruckRow
          key={truck.id}
          truck={truck}
          filters={filters}
          position={{ index: idx, total: filtered.length }}
          onReorder={(direction) => reorder(truck.id, direction)}
          hideWhenEmpty={hideEmptyRows}
        />
      ))}
    </div>
  );
}
