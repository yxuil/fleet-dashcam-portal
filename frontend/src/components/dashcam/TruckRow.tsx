/**
 * One Fleet Cam row: truck label + reorder arrows on the left, a
 * horizontally scrolling strip of day cards on the right.
 *
 * The list of days comes from `useTruckDays(truck.id, filters)`. Up/down
 * arrows are wired to the parent via `onReorder` — the parent owns the
 * actual order array and the mutation that persists it.
 */

import { useTruckDays, type TruckDaysFilters } from "@/hooks/useTruckDays";
import type { TruckOut } from "@/lib/types";
import { cn } from "@/lib/utils";

import { DayCard } from "./DayCard";

export type TruckRowProps = {
  truck: TruckOut;
  filters: TruckDaysFilters;
  position: { index: number; total: number };
  onReorder: (direction: "up" | "down") => void;
  /** When true the empty state hides the row entirely (driver filter). */
  hideWhenEmpty?: boolean;
};

export function TruckRow({
  truck,
  filters,
  position,
  onReorder,
  hideWhenEmpty,
}: TruckRowProps) {
  const days = useTruckDays(truck.id, filters);
  const items = days.data ?? [];

  if (
    hideWhenEmpty &&
    !days.isLoading &&
    !days.isError &&
    items.length === 0
  ) {
    return null;
  }

  return (
    <section
      data-testid={`truck-row-${truck.id}`}
      className="flex items-stretch gap-4 rounded-lg border border-border bg-background p-4"
    >
      <div className="flex w-44 shrink-0 flex-col justify-between">
        <div>
          <div
            className="text-sm font-semibold text-foreground"
            data-testid="truck-row-label"
          >
            {truck.label}
          </div>
          {truck.vin ? (
            <div className="text-xs text-muted-foreground">{truck.vin}</div>
          ) : null}
        </div>
        <div className="flex gap-1">
          <button
            type="button"
            disabled={position.index === 0}
            onClick={() => onReorder("up")}
            data-testid={`truck-row-${truck.id}-up`}
            aria-label={`Move ${truck.label} up`}
            className={cn(
              "h-7 w-7 rounded-md border border-border bg-background text-xs",
              "hover:bg-accent disabled:opacity-30 disabled:hover:bg-background",
            )}
          >
            ↑
          </button>
          <button
            type="button"
            disabled={position.index === position.total - 1}
            onClick={() => onReorder("down")}
            data-testid={`truck-row-${truck.id}-down`}
            aria-label={`Move ${truck.label} down`}
            className={cn(
              "h-7 w-7 rounded-md border border-border bg-background text-xs",
              "hover:bg-accent disabled:opacity-30 disabled:hover:bg-background",
            )}
          >
            ↓
          </button>
        </div>
      </div>

      <div
        className="flex flex-1 gap-3 overflow-x-auto pb-1"
        data-testid="truck-row-days"
      >
        {days.isLoading ? (
          <RowSkeleton />
        ) : days.isError ? (
          <p className="text-xs text-muted-foreground">
            Couldn’t load days for {truck.label}.
          </p>
        ) : items.length === 0 ? (
          <p
            className="self-center text-xs text-muted-foreground"
            data-testid="truck-row-empty"
          >
            No clips in range.
          </p>
        ) : (
          items.map((d) => <DayCard key={d.first_clip_id} day={d} />)
        )}
      </div>
    </section>
  );
}

function RowSkeleton() {
  return (
    <>
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={i}
          data-testid="day-card-skeleton"
          className="flex h-32 w-40 shrink-0 animate-pulse flex-col gap-2 rounded-lg border border-border bg-card p-3"
        >
          <div className="h-16 w-full rounded bg-muted" />
          <div className="h-3 w-2/3 rounded bg-muted" />
          <div className="h-2 w-1/2 rounded bg-muted" />
        </div>
      ))}
    </>
  );
}
