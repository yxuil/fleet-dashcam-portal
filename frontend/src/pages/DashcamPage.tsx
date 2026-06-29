/**
 * Fleet Cam — `/dashcam`.
 *
 * The portal's landing page. Replaces the old flat Search grid with
 * one row per truck and a horizontal strip of day cards, modeled on
 * YouTube's "one row per channel" layout. Filters and row order
 * persist server-side via `/me/preferences` and across reloads via
 * the URL.
 *
 * URL parameters:
 *   - `truck_id`   single-truck filter (one row visible)
 *   - `driver_id`  single-driver filter (only trucks the driver drove)
 *   - `from`, `to` ISO timestamps narrowing the day window
 *   - `q`          free-text filter on truck label (client-side only)
 */

import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import { TruckDriverPicker } from "@/components/dashcam/TruckDriverPicker";
import { TruckRowList } from "@/components/dashcam/TruckRowList";
import { useDrivers } from "@/hooks/useDrivers";
import { useTrucks } from "@/hooks/useTrucks";

const PARAM_TRUCK = "truck_id";
const PARAM_DRIVER = "driver_id";
const PARAM_FROM = "from";
const PARAM_TO = "to";
const PARAM_TEXT = "q";

type PageFilters = {
  truck_id?: string;
  driver_id?: string;
  from?: string;
  to?: string;
  q?: string;
};

function parseFilters(params: URLSearchParams): PageFilters {
  return {
    truck_id: params.get(PARAM_TRUCK) ?? undefined,
    driver_id: params.get(PARAM_DRIVER) ?? undefined,
    from: params.get(PARAM_FROM) ?? undefined,
    to: params.get(PARAM_TO) ?? undefined,
    q: params.get(PARAM_TEXT) ?? undefined,
  };
}

function filtersToParams(filters: PageFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.truck_id) params.set(PARAM_TRUCK, filters.truck_id);
  if (filters.driver_id) params.set(PARAM_DRIVER, filters.driver_id);
  if (filters.from) params.set(PARAM_FROM, filters.from);
  if (filters.to) params.set(PARAM_TO, filters.to);
  if (filters.q && filters.q.trim()) params.set(PARAM_TEXT, filters.q);
  return params;
}

export function DashcamPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(searchParams), [searchParams]);

  const setFilters = useCallback(
    (next: PageFilters) => {
      setSearchParams(filtersToParams(next), { replace: true });
    },
    [setSearchParams],
  );

  const trucks = useTrucks();
  const drivers = useDrivers();

  const truckIds = filters.truck_id ? [filters.truck_id] : undefined;

  return (
    <section className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Fleet Cam</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Each row is a truck. Each card is a day of recorded clips.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <TruckDriverPicker
          trucks={trucks.data ?? []}
          drivers={drivers.data ?? []}
          value={{
            truck_id: filters.truck_id,
            driver_id: filters.driver_id,
          }}
          onChange={(next) =>
            setFilters({
              ...filters,
              truck_id: next.truck_id,
              driver_id: next.driver_id,
            })
          }
        />
        <label className="flex flex-col text-xs text-muted-foreground">
          <span className="mb-0.5">From</span>
          <input
            type="date"
            value={filters.from ?? ""}
            data-testid="dashcam-from"
            onChange={(e) =>
              setFilters({ ...filters, from: e.target.value || undefined })
            }
            className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
          />
        </label>
        <label className="flex flex-col text-xs text-muted-foreground">
          <span className="mb-0.5">To</span>
          <input
            type="date"
            value={filters.to ?? ""}
            data-testid="dashcam-to"
            onChange={(e) =>
              setFilters({ ...filters, to: e.target.value || undefined })
            }
            className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
          />
        </label>
        <label className="flex flex-1 flex-col text-xs text-muted-foreground">
          <span className="mb-0.5">Search trucks</span>
          <input
            type="text"
            value={filters.q ?? ""}
            data-testid="dashcam-q"
            placeholder="e.g. 101"
            onChange={(e) =>
              setFilters({ ...filters, q: e.target.value || undefined })
            }
            className="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground"
          />
        </label>
      </div>

      <TruckRowList
        filters={{
          // Convert YYYY-MM-DD date inputs to ISO datetime; backend
          // accepts either, but we standardise on midnight UTC.
          from: filters.from ? `${filters.from}T00:00:00Z` : undefined,
          to: filters.to ? `${filters.to}T23:59:59Z` : undefined,
          driver_id: filters.driver_id ?? undefined,
        }}
        truckIds={truckIds}
        textQuery={filters.q}
        hideEmptyRows={Boolean(filters.driver_id)}
      />
    </section>
  );
}
