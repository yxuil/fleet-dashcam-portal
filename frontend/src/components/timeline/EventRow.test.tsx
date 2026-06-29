/**
 * Tests for `<EventRow />`.
 *
 * The row is a pure presentational component — no data fetching, no
 * route awareness — so the tests poke at props directly and assert the
 * rendered output. The triage dropdown is tested through the
 * `<Dropdown>` it uses (we click the trigger first, then the menu item).
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { EventRow as EventRowData } from "@/lib/types";

import { EventRow } from "./EventRow";
import { formatTelemetrySnippet } from "./telemetry";

function makeEvent(overrides: Partial<EventRowData> = {}): EventRowData {
  return {
    id: "ev-1",
    tenant_id: "tn",
    truck_id: "t1",
    truck_label: "T-1",
    driver_id: null,
    driver_name: null,
    clip_id: "clip-1",
    occurred_at: "2026-06-29T12:00:00Z",
    type: "harsh_brake",
    severity: "high",
    telemetry: { speed_kmh: 64, accel_g: 1.2 },
    gps_lat: null,
    gps_lng: null,
    ...overrides,
  };
}

describe("EventRow", () => {
  it("renders timestamp, type, severity badge, telemetry snippet", () => {
    const event = makeEvent();
    render(
      <ul>
        <EventRow
          event={event}
          triagedAs={null}
          onOpenClip={vi.fn()}
          onTriage={vi.fn()}
        />
      </ul>,
    );

    expect(screen.getByTestId(`event-type-${event.id}`)).toHaveTextContent(
      "Harsh brake",
    );
    expect(
      screen.getByTestId(`event-severity-${event.id}`),
    ).toHaveTextContent("high");
    expect(
      screen.getByTestId(`event-telemetry-${event.id}`),
    ).toHaveTextContent("speed 64 km/h, ΔG 1.2");
  });

  it("colours the severity badge by level", () => {
    for (const sev of ["critical", "high", "medium", "low"] as const) {
      const event = makeEvent({ id: `ev-${sev}`, severity: sev });
      render(
        <ul>
          <EventRow
            event={event}
            triagedAs={null}
            onOpenClip={vi.fn()}
            onTriage={vi.fn()}
          />
        </ul>,
      );
      const badge = screen.getByTestId(`event-severity-${event.id}`);
      // Smoke test: each severity gets a distinct background colour
      // class. We don't pin the literal class names so a theme tweak
      // doesn't require a test rewrite.
      const cls = badge.className;
      if (sev === "critical") expect(cls).toMatch(/red/);
      if (sev === "high") expect(cls).toMatch(/orange/);
      if (sev === "medium") expect(cls).toMatch(/yellow/);
      if (sev === "low") expect(cls).toMatch(/slate/);
    }
  });

  it("invokes onOpenClip with the clip id when the button is clicked", () => {
    const event = makeEvent();
    const onOpenClip = vi.fn();
    render(
      <ul>
        <EventRow
          event={event}
          triagedAs={null}
          onOpenClip={onOpenClip}
          onTriage={vi.fn()}
        />
      </ul>,
    );
    fireEvent.click(screen.getByTestId(`event-open-clip-${event.id}`));
    expect(onOpenClip).toHaveBeenCalledWith("clip-1");
  });

  it("disables open-clip and open-case when clip_id is null", () => {
    const event = makeEvent({ clip_id: null });
    render(
      <ul>
        <EventRow
          event={event}
          triagedAs={null}
          onOpenClip={vi.fn()}
          onTriage={vi.fn()}
        />
      </ul>,
    );
    expect(screen.getByTestId(`event-open-clip-${event.id}`)).toBeDisabled();

    // Open the triage menu, then check the "Open case" item is disabled.
    fireEvent.click(screen.getByTestId(`event-triage-trigger-${event.id}`));
    expect(
      screen.getByTestId(`event-triage-open-case-${event.id}`),
    ).toBeDisabled();
  });

  it("calls onTriage with the picked label", () => {
    const event = makeEvent();
    const onTriage = vi.fn();
    render(
      <ul>
        <EventRow
          event={event}
          triagedAs={null}
          onOpenClip={vi.fn()}
          onTriage={onTriage}
        />
      </ul>,
    );
    fireEvent.click(screen.getByTestId(`event-triage-trigger-${event.id}`));
    fireEvent.click(screen.getByTestId(`event-triage-fp-${event.id}`));
    expect(onTriage).toHaveBeenCalledWith("false_positive");
  });

  it("shows an inline triage badge when triagedAs is set", () => {
    const event = makeEvent();
    render(
      <ul>
        <EventRow
          event={event}
          triagedAs="coaching_note"
          onOpenClip={vi.fn()}
          onTriage={vi.fn()}
        />
      </ul>,
    );
    expect(screen.getByTestId(`event-triaged-${event.id}`)).toHaveTextContent(
      "Coaching note",
    );
  });
});

describe("formatTelemetrySnippet", () => {
  it("renders both speed and ΔG when present", () => {
    expect(formatTelemetrySnippet({ speed_kmh: 50, accel_g: 0.8 })).toBe(
      "speed 50 km/h, ΔG 0.8",
    );
  });

  it("omits missing fields cleanly", () => {
    expect(formatTelemetrySnippet({ speed_kmh: 30 })).toBe("speed 30 km/h");
    expect(formatTelemetrySnippet({ accel_g: 1.5 })).toBe("ΔG 1.5");
    expect(formatTelemetrySnippet({})).toBe("");
  });

  it("ignores non-numeric telemetry values", () => {
    expect(
      formatTelemetrySnippet({ speed_kmh: "fast" as unknown as number }),
    ).toBe("");
  });
});
