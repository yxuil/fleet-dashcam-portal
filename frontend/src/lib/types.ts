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

/**
 * One day's worth of clips for a truck — served by
 * `GET /trucks/{id}/days`. The Fleet Cam row scroller renders one of
 * these per card; `first_clip_id` is the click-through target.
 */
export type TruckDay = {
  date: string;
  clip_count: number;
  first_clip_id: string;
  total_duration_s: number;
};

/**
 * Per-user opaque preferences blob served by `GET /me/preferences`.
 *
 * `truck_order` is the only known key today (Fleet Cam row ordering);
 * the index signature lets us round-trip future keys without churn.
 */
export type Preferences = {
  truck_order?: string[];
  [k: string]: unknown;
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

/**
 * Case status enum — mirrors `app.models.case.CaseStatus`.
 *
 * `"closed"` is a valid value here (returned by the backend) but is NOT
 * an allowed value for `PATCH /cases/:id` — the dedicated
 * `POST /cases/:id/close` endpoint must be used so a reason is captured.
 * See `PatchableCaseStatus` for the patchable subset.
 */
export const CASE_STATUSES = [
  "open",
  "under_review",
  "approved",
  "closed",
] as const;
export type CaseStatus = (typeof CASE_STATUSES)[number];

/** Statuses the user can transition a case to via PATCH. */
export const PATCHABLE_CASE_STATUSES = [
  "open",
  "under_review",
  "approved",
] as const;
export type PatchableCaseStatus = (typeof PATCHABLE_CASE_STATUSES)[number];

/**
 * One audit row in API responses — mirrors `app.audit.AuditEntry`.
 *
 * `payload` is arbitrary JSON. The Notes tab inspects `payload.text`
 * for `case.note_added` rows; everything else renders the payload
 * generically.
 */
export type AuditEntry = {
  id: number;
  tenant_id: string;
  actor_user_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  payload: Record<string, unknown>;
  occurred_at: string;
};

/** One attached-clip entry inside `CaseDetail`. */
export type AttachedClip = {
  clip_id: string;
  attached_at: string;
  attached_by: string;
  note: string | null;
  truck_label: string;
  started_at: string;
};

/** Compact case row served by `GET /cases`. */
export type CaseRow = {
  id: string;
  tenant_id: string;
  number: string;
  external_ref: string | null;
  requester_name: string | null;
  requester_org: string | null;
  incident_at: string | null;
  status: CaseStatus;
  assignee_user_id: string | null;
  due_at: string | null;
  created_by: string;
  created_at: string;
};

/** Full case detail served by `GET /cases/:id`. */
export type CaseDetail = CaseRow & {
  clips: AttachedClip[];
  recent_audit: AuditEntry[];
};

/** Paginated wrapper for `GET /cases`. */
export type CaseListResponse = {
  items: CaseRow[];
  next_cursor: string | null;
};
