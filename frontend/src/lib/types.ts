/**
 * Shared API response shapes mirroring the FastAPI Pydantic models.
 *
 * Keep this file lean — only the shapes actually consumed by the
 * frontend. If a property exists on the backend response but the UI
 * doesn't need it, leave it out so a casual reader knows what we use.
 */

/** Compact clip row served by `GET /clips`. */
export type ClipRow = {
  id: string;
  tenant_id: string;
  truck_id: string;
  truck_label: string;
  driver_id: string | null;
  driver_name: string | null;
  started_at: string;
  ended_at: string;
  duration_s: number;
  storage_key: string;
  ingested_at: string;
};

/**
 * Full clip detail served by `GET /clips/{id}`.
 *
 * `playback_url` is only populated when the request is made with
 * `?play=true` — otherwise it's `null` and we shouldn't try to render a
 * `<video>` source from it.
 */
export type ClipDetail = ClipRow & {
  sha256: string | null;
  dashcam_firmware: string | null;
  playback_url: string | null;
};

/** Paginated wrapper for `GET /clips`. */
export type ClipListResponse = {
  items: ClipRow[];
  next_cursor: string | null;
};

/** Truck row served by `GET /trucks` and `GET /trucks/{id}`. */
export type TruckOut = {
  id: string;
  tenant_id: string;
  label: string;
  vin: string | null;
  dashcam_serial: string | null;
  last_seen_at: string | null;
};

/** Driver row served by `GET /drivers`. */
export type DriverOut = {
  id: string;
  tenant_id: string;
  name: string;
  employee_ref: string | null;
};

/**
 * Event type / severity enums — mirror the backend `StrEnum`s. Useful
 * for filter chips even though `GET /clips` doesn't (yet) filter on
 * them. T13 will use these for the event timeline.
 */
export const EVENT_TYPES = [
  "harsh_brake",
  "harsh_accel",
  "collision",
  "lane_departure",
  "speeding",
  "distracted_driving",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export const EVENT_SEVERITIES = ["critical", "high", "medium", "low"] as const;
export type EventSeverity = (typeof EVENT_SEVERITIES)[number];

/**
 * Event row served by `GET /events`.
 *
 * Mirrors the backend `EventRow` Pydantic model. The video-player page
 * uses these to render harsh-event markers on the clip timeline;
 * `telemetry.speed_kmh` (when present) feeds the speed read-out.
 */
export type EventRow = {
  id: string;
  tenant_id: string;
  truck_id: string;
  truck_label: string;
  driver_id: string | null;
  driver_name: string | null;
  clip_id: string | null;
  occurred_at: string;
  type: EventType;
  severity: EventSeverity;
  telemetry: Record<string, unknown>;
  gps_lat: number | null;
  gps_lng: number | null;
};

/** Paginated wrapper for `GET /events`. */
export type EventListResponse = {
  items: EventRow[];
  next_cursor: string | null;
};
