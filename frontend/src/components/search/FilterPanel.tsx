/**
 * Sidebar filter panel for the Search page.
 *
 * Responsibilities:
 *   - Render truck/driver multi-select (plain checkbox lists — no fancy
 *     combobox dependency required for the MVP).
 *   - Render a from/to datetime-local range.
 *   - Render a free-text input.
 *   - Render disabled event-type and severity chips with a "coming in v2"
 *     hint so the panel feels complete without lying about capability.
 *   - Surface a "Clear all" affordance.
 *
 * Everything is uncontrolled by design: the parent owns `filters` and
 * the panel calls `onChange` with the next snapshot. The parent is
 * responsible for debouncing before issuing the network request — see
 * `useDebouncedValue` in SearchPage.
 */

import { useMemo } from "react";

import { Button } from "@/components/ui/Button";
import type { ClipFilters } from "@/hooks/useClips";
import { EMPTY_FILTERS } from "@/hooks/useClips";
import type { DriverOut, TruckOut } from "@/lib/types";
import { EVENT_SEVERITIES, EVENT_TYPES } from "@/lib/types";
import { cn } from "@/lib/utils";

type Props = {
  filters: ClipFilters;
  onChange: (next: ClipFilters) => void;
  trucks: readonly TruckOut[];
  drivers: readonly DriverOut[];
  trucksLoading?: boolean;
  driversLoading?: boolean;
};

function toggleId(list: readonly string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

export function FilterPanel({
  filters,
  onChange,
  trucks,
  drivers,
  trucksLoading,
  driversLoading,
}: Props) {
  const hasAny = useMemo(() => {
    return (
      filters.truckIds.length > 0 ||
      filters.driverIds.length > 0 ||
      filters.from !== null ||
      filters.to !== null ||
      filters.text.trim().length > 0
    );
  }, [filters]);

  return (
    <aside
      aria-label="Search filters"
      className="w-full shrink-0 space-y-6 lg:w-72"
      data-testid="filter-panel"
    >
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Filters
        </h2>
        {hasAny ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onChange(EMPTY_FILTERS)}
            data-testid="clear-filters"
          >
            Clear all
          </Button>
        ) : null}
      </header>

      <Section title="Search">
        <input
          type="search"
          placeholder="Truck or driver…"
          value={filters.text}
          onChange={(e) => onChange({ ...filters, text: e.target.value })}
          aria-label="Free-text search"
          data-testid="filter-text"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </Section>

      <Section title="Date range">
        <div className="space-y-2">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            From
            <input
              type="datetime-local"
              value={filters.from ?? ""}
              onChange={(e) =>
                onChange({ ...filters, from: e.target.value || null })
              }
              data-testid="filter-from"
              className="rounded-md border border-input bg-background px-2 py-1 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            To
            <input
              type="datetime-local"
              value={filters.to ?? ""}
              onChange={(e) =>
                onChange({ ...filters, to: e.target.value || null })
              }
              data-testid="filter-to"
              className="rounded-md border border-input bg-background px-2 py-1 text-sm"
            />
          </label>
        </div>
      </Section>

      <Section title="Trucks">
        <CheckboxList
          items={trucks.map((t) => ({ id: t.id, label: t.label }))}
          loading={trucksLoading}
          selected={filters.truckIds}
          onToggle={(id) =>
            onChange({ ...filters, truckIds: toggleId(filters.truckIds, id) })
          }
          emptyLabel="No trucks yet."
          testId="filter-trucks"
        />
      </Section>

      <Section title="Drivers">
        <CheckboxList
          items={drivers.map((d) => ({ id: d.id, label: d.name }))}
          loading={driversLoading}
          selected={filters.driverIds}
          onToggle={(id) =>
            onChange({
              ...filters,
              driverIds: toggleId(filters.driverIds, id),
            })
          }
          emptyLabel="No drivers yet."
          testId="filter-drivers"
        />
      </Section>

      <Section
        title="Event type"
        hint="Filtering events is coming in v2."
      >
        <ChipRow values={EVENT_TYPES} disabled />
      </Section>

      <Section title="Severity" hint="Filtering events is coming in v2.">
        <ChipRow values={EVENT_SEVERITIES} disabled />
      </Section>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </h3>
        {hint ? (
          <span
            title={hint}
            className="text-[10px] uppercase text-muted-foreground/70"
          >
            v2
          </span>
        ) : null}
      </div>
      {children}
    </section>
  );
}

type CheckboxItem = { id: string; label: string };

function CheckboxList({
  items,
  loading,
  selected,
  onToggle,
  emptyLabel,
  testId,
}: {
  items: readonly CheckboxItem[];
  loading?: boolean;
  selected: readonly string[];
  onToggle: (id: string) => void;
  emptyLabel: string;
  testId?: string;
}) {
  if (loading) {
    return (
      <p className="text-xs text-muted-foreground">Loading…</p>
    );
  }
  if (items.length === 0) {
    return <p className="text-xs text-muted-foreground">{emptyLabel}</p>;
  }
  return (
    <ul
      data-testid={testId}
      className="max-h-48 space-y-1 overflow-y-auto rounded-md border border-border bg-background p-2"
    >
      {items.map((item) => {
        const checked = selected.includes(item.id);
        return (
          <li key={item.id}>
            <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-accent">
              <input
                type="checkbox"
                checked={checked}
                onChange={() => onToggle(item.id)}
                aria-label={item.label}
                data-testid={`${testId}-${item.id}`}
              />
              <span className="truncate">{item.label}</span>
            </label>
          </li>
        );
      })}
    </ul>
  );
}

function ChipRow({
  values,
  disabled,
}: {
  values: readonly string[];
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {values.map((v) => (
        <button
          key={v}
          type="button"
          disabled={disabled}
          title={disabled ? "Coming in v2" : undefined}
          aria-disabled={disabled}
          className={cn(
            "rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-[11px] font-medium capitalize text-muted-foreground",
            disabled && "cursor-not-allowed opacity-60",
          )}
        >
          {v.replace(/_/g, " ")}
        </button>
      ))}
    </div>
  );
}
