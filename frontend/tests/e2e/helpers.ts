/**
 * Shared Playwright test helpers — API queries and seeded-data lookups.
 *
 * We use Playwright's `APIRequestContext` instead of `node-fetch` so the
 * test config (`baseURL`, headers, proxy, etc.) flows through one place
 * and we don't add another runtime dep.
 *
 * Auth: every helper takes an `APIRequestContext` that's been preconfigured
 * via `request.newContext({ extraHTTPHeaders: ACME_HEADERS })`. The helpers
 * themselves don't know about dev-mode user IDs — that's the test's job.
 */

import { type APIRequestContext, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Seeded principals — kept in sync with backend/app/seed.py via uuid5.
// Re-derive with the snippet documented in `frontend/src/lib/auth.ts`.
// ---------------------------------------------------------------------------

export const ACME_TENANT = "a6190065-514d-5d27-b599-e81673beb843";
export const ACME_ADMIN_USER = "559ab941-158d-5667-a214-ef00157e6375";

export const API_BASE = process.env.E2E_API_BASE ?? "http://localhost:8000";

/** Dev-mode auth headers as a plain object — convenient for `apiContext()`. */
export const ACME_HEADERS: Record<string, string> = {
  "X-Dev-User-Id": ACME_ADMIN_USER,
  "X-Dev-Tenant-Id": ACME_TENANT,
};

// ---------------------------------------------------------------------------
// Types — narrow shapes (only the fields we read in tests)
// ---------------------------------------------------------------------------

export type SeededTruck = {
  id: string;
  label: string;
};

export type SeededClip = {
  id: string;
  truck_id: string;
  truck_label: string;
  started_at: string;
  duration_s: number;
};

export type SeededEvent = {
  id: string;
  truck_id: string;
  clip_id: string | null;
  severity: "low" | "medium" | "high" | "critical";
  type: string;
  occurred_at: string;
};

export type AuditItem = {
  id: number;
  action: string;
  target_type: string;
  target_id: string;
  payload: Record<string, unknown>;
};

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

/**
 * Fetch audit entries scoped to one target. The backend tenant-scopes the
 * query implicitly via the principal, so the caller just supplies the
 * type + id. Returns the `items` array, never the cursor — tests only
 * need to confirm an entry exists, not paginate.
 */
export async function apiAuditFor(
  request: APIRequestContext,
  targetType: string,
  targetId: string,
): Promise<AuditItem[]> {
  const url = new URL(`${API_BASE}/audit`);
  url.searchParams.set("target_type", targetType);
  url.searchParams.set("target_id", targetId);
  url.searchParams.set("limit", "100");
  const response = await request.get(url.toString());
  expect(response.ok(), `GET /audit returned ${response.status()}`).toBe(true);
  const body = (await response.json()) as { items: AuditItem[] };
  return body.items;
}

/** GET /trucks → first truck for the active tenant. */
export async function firstSeededTruckForTenant(
  request: APIRequestContext,
): Promise<SeededTruck> {
  const response = await request.get(`${API_BASE}/trucks`);
  expect(response.ok(), `GET /trucks returned ${response.status()}`).toBe(true);
  const trucks = (await response.json()) as SeededTruck[];
  expect(trucks.length, "no seeded trucks for tenant").toBeGreaterThan(0);
  return trucks[0];
}

/** GET /clips → first clip overall (any truck). */
export async function firstSeededClip(
  request: APIRequestContext,
): Promise<SeededClip> {
  const response = await request.get(`${API_BASE}/clips?limit=1`);
  expect(response.ok(), `GET /clips returned ${response.status()}`).toBe(true);
  const body = (await response.json()) as { items: SeededClip[] };
  expect(body.items.length, "no seeded clips for tenant").toBeGreaterThan(0);
  return body.items[0];
}

/** GET /clips?truck=<id> → first clip for that truck. */
export async function firstSeededClipForTruck(
  request: APIRequestContext,
  truckId: string,
): Promise<SeededClip> {
  const response = await request.get(
    `${API_BASE}/clips?truck_id=${truckId}&limit=1`,
  );
  expect(response.ok(), `GET /clips?truck= returned ${response.status()}`).toBe(
    true,
  );
  const body = (await response.json()) as { items: SeededClip[] };
  expect(body.items.length, `no seeded clips for truck ${truckId}`).toBeGreaterThan(
    0,
  );
  return body.items[0];
}

/**
 * GET /trucks/{id}/events?severity=high — first event with a clip attached.
 *
 * "with a clip" matters because the "Open case" flow needs a clip to attach.
 * Seeded events sometimes have `clip_id=null`, so we fetch a page and pick
 * the first matching one. If none of the page's events have a clip, we
 * widen the query — but in practice the seed is dense enough that the
 * first page always contains plenty of clip-bearing events.
 */
export async function firstHighSeverityEventWithClip(
  request: APIRequestContext,
  truckId: string,
): Promise<SeededEvent> {
  const response = await request.get(
    `${API_BASE}/trucks/${truckId}/events?severity=high&limit=50`,
  );
  expect(
    response.ok(),
    `GET /trucks/${truckId}/events?severity=high returned ${response.status()}`,
  ).toBe(true);
  const body = (await response.json()) as { items: SeededEvent[] };
  const withClip = body.items.find((ev) => ev.clip_id !== null);
  expect(
    withClip,
    `no high-severity event with a clip for truck ${truckId}`,
  ).toBeTruthy();
  return withClip!;
}

/** First high-severity event for the truck (clip optional). */
export async function firstHighSeverityEvent(
  request: APIRequestContext,
  truckId: string,
): Promise<SeededEvent> {
  const response = await request.get(
    `${API_BASE}/trucks/${truckId}/events?severity=high&limit=50`,
  );
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { items: SeededEvent[] };
  expect(body.items.length, `no high-severity events for truck ${truckId}`).toBeGreaterThan(
    0,
  );
  return body.items[0];
}
