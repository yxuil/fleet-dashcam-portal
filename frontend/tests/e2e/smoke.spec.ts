/**
 * End-to-end smoke tests for the dashcam portal MVP.
 *
 * Three independent scenarios:
 *
 *   (a) Search → play a clip → confirm `clip.play_url_minted` audit row.
 *   (b) Timeline → triage event as "coaching_note" → confirm badge + audit.
 *   (c) Timeline → "Open case" → attach a clip → mark under_review →
 *       close with reason → confirm Activity tab lists each step.
 *
 * Each scenario authenticates fresh by seeding localStorage with the Acme
 * admin dev-user IDs via `page.addInitScript` (runs before any page script).
 *
 * API assertions go through a dedicated `request.newContext` that carries
 * the same dev headers — that way audit checks bypass the React app and
 * we're not relying on console.log scraping.
 *
 * Resilience notes:
 *   - We deliberately avoid `page.waitForTimeout`. Playwright's auto-wait
 *     (locator interactions wait for actionability) plus `expect().toBeVisible()`
 *     with a generous timeout covers the legitimate async gaps.
 *   - Scenario (a)'s video assertion is intentionally lax: we just check the
 *     `<video>` got a backend-pointing `src` (local-mode stream route on
 *     :8000 in the T17 default, or a MinIO URL on :9000 in s3 mode).
 *     Headless Chromium may not successfully decode every clip, and that
 *     isn't what we're testing — the audit row IS the assertion that the
 *     play URL was minted server-side.
 */

import { test, expect, type Page } from "@playwright/test";

import {
  ACME_ADMIN_USER,
  ACME_HEADERS,
  ACME_TENANT,
  API_BASE,
  apiAuditFor,
  firstHighSeverityEventWithClip,
  firstSeededTruckForTenant,
} from "./helpers";

// ---------------------------------------------------------------------------
// Fixtures: every test starts as the Acme admin dev user.
// ---------------------------------------------------------------------------

/**
 * Inject the dev-user / dev-tenant identifiers into localStorage BEFORE
 * the React app boots. The frontend reads these on every API call via
 * `getDevHeaders()` (see `frontend/src/lib/auth.ts`), so by setting them
 * up front we sidestep the DevUserPicker click entirely.
 */
async function authAsAcmeAdmin(page: Page): Promise<void> {
  await page.addInitScript(
    ({ uid, tid }) => {
      // Keys match `STORAGE_USER_KEY` / `STORAGE_TENANT_KEY` in auth.ts.
      window.localStorage.setItem("dashcam.dev_user", uid);
      window.localStorage.setItem("dashcam.dev_tenant", tid);
    },
    { uid: ACME_ADMIN_USER, tid: ACME_TENANT },
  );
}

// ---------------------------------------------------------------------------
// Scenario (a) — Fleet Cam picker → day card → play
// ---------------------------------------------------------------------------

test("(a) fleet cam → pick truck → click day card → audit shows play_url_minted", async ({
  page,
  playwright,
}) => {
  await authAsAcmeAdmin(page);

  // Use a parallel API context (with dev headers) for backend assertions.
  const api = await playwright.request.newContext({
    extraHTTPHeaders: ACME_HEADERS,
  });

  // Pre-flight: pick a truck so we know which row to drive in the UI.
  const truck = await firstSeededTruckForTenant(api);

  await page.goto("/dashcam");

  // The bottom-right dev user picker should mount and show our active user.
  // It's our visible proof that localStorage injection worked.
  await expect(page.getByTestId("dev-user-picker")).toBeVisible();
  await expect(page.getByTestId("dev-user-picker")).toContainText("Acme");

  // Open the truck/driver picker and select our truck via its row in the
  // Truck section. The testid is `truck-driver-picker-truck-<truckId>`.
  await page.getByTestId("truck-driver-picker-trigger").click();
  await expect(page.getByTestId("truck-driver-picker-popover")).toBeVisible();
  await page.getByTestId(`truck-driver-picker-truck-${truck.id}`).click();
  await page.getByTestId("truck-driver-picker-apply").click();
  await expect(page).toHaveURL(new RegExp(`truck_id=${truck.id}`));

  // Wait for the truck row to mount, then click the first day card.
  const row = page.getByTestId(`truck-row-${truck.id}`);
  await expect(row).toBeVisible({ timeout: 10_000 });
  const card = row.locator("[data-testid^=\"day-card-\"]").first();
  await expect(card).toBeVisible({ timeout: 10_000 });
  await card.click();

  // Land on detail. The card opens the day's first clip; we don't know
  // its id ahead of time, so just match the shape of the URL.
  await expect(page).toHaveURL(/\/clips\/[0-9a-f-]{36}$/);
  const clipId = new URL(page.url()).pathname.split("/").pop()!;

  // The video element should mount with a backend-pointing src once the
  // detail GET resolves. Accept either local stream route or MinIO URL.
  const video = page.getByTestId("clip-video");
  await expect(video).toBeVisible({ timeout: 10_000 });
  await expect(video).toHaveAttribute(
    "src",
    /^http:\/\/localhost:(8000\/clips\/[0-9a-f-]+\/stream|9000\/)/,
    { timeout: 10_000 },
  );

  // The backend writes `clip.play_url_minted` on every GET /clips/:id?play=true.
  const audit = await apiAuditFor(api, "clip", clipId);
  const minted = audit.find((a) => a.action === "clip.play_url_minted");
  expect(
    minted,
    `expected a clip.play_url_minted audit for clip ${clipId}; got actions: ${audit
      .map((a) => a.action)
      .join(", ")}`,
  ).toBeTruthy();

  await api.dispose();
});

// ---------------------------------------------------------------------------
// Scenario (d) — reorder persists across reloads
// ---------------------------------------------------------------------------

test("(d) reorder truck row persists across reload", async ({ page }) => {
  await authAsAcmeAdmin(page);

  await page.goto("/dashcam");

  // Capture the labels of the first two visible rows.
  const rows = page.locator("[data-testid^=\"truck-row-\"]");
  await expect(rows.first()).toBeVisible({ timeout: 10_000 });
  const firstLabelBefore = await rows
    .first()
    .getByTestId("truck-row-label")
    .innerText();
  const secondLabelBefore = await rows
    .nth(1)
    .getByTestId("truck-row-label")
    .innerText();

  // Move the first row down. The button id encodes the truck id, so we
  // resolve it from the row's data-testid attribute.
  const firstRowTestId = await rows.first().getAttribute("data-testid");
  const truckId = firstRowTestId!.replace("truck-row-", "");
  await page.getByTestId(`truck-row-${truckId}-down`).click();

  // Reload and confirm the order has flipped.
  await page.reload();
  await expect(rows.first()).toBeVisible({ timeout: 10_000 });
  const firstLabelAfter = await rows
    .first()
    .getByTestId("truck-row-label")
    .innerText();
  const secondLabelAfter = await rows
    .nth(1)
    .getByTestId("truck-row-label")
    .innerText();
  expect(firstLabelAfter).toBe(secondLabelBefore);
  expect(secondLabelAfter).toBe(firstLabelBefore);
});

// ---------------------------------------------------------------------------
// Scenario (b) — Triage as coaching_note
// ---------------------------------------------------------------------------

test("(b) triage event as coaching_note → badge + audit row", async ({
  page,
  playwright,
}) => {
  await authAsAcmeAdmin(page);
  const api = await playwright.request.newContext({
    extraHTTPHeaders: ACME_HEADERS,
  });

  const truck = await firstSeededTruckForTenant(api);
  const event = await firstHighSeverityEventWithClip(api, truck.id);

  await page.goto(`/trucks/${truck.id}/events`);

  // Switch to the "high" severity tab. The "all" tab is the default; we
  // tap "high" specifically per the scenario.
  await page.getByTestId("severity-tab-high").click();

  // Wait for the row corresponding to our chosen event to appear.
  const row = page.getByTestId(`event-row-${event.id}`);
  await expect(row).toBeVisible({ timeout: 10_000 });

  // Open the row's triage dropdown, then pick "Coaching note".
  await page.getByTestId(`event-triage-trigger-${event.id}`).click();
  await page.getByTestId(`event-triage-coach-${event.id}`).click();

  // The row should now show an inline triage badge. We assert on the text
  // content rather than the testid alone so we know the right label landed.
  const badge = page.getByTestId(`event-triaged-${event.id}`);
  await expect(badge).toBeVisible({ timeout: 10_000 });
  await expect(badge).toContainText(/coaching/i);

  // And the audit row should be present.
  const audit = await apiAuditFor(api, "event", event.id);
  const triage = audit.find(
    (a) =>
      a.action === "event.triage" &&
      (a.payload as { label?: string }).label === "coaching_note",
  );
  expect(
    triage,
    `expected an event.triage(coaching_note) audit for event ${event.id}; got: ${JSON.stringify(audit)}`,
  ).toBeTruthy();

  await api.dispose();
});

// ---------------------------------------------------------------------------
// Scenario (c) — Open case → attach → under_review → close
// ---------------------------------------------------------------------------

test("(c) open case → attach clip → under_review → close → activity lists steps", async ({
  page,
  playwright,
}) => {
  await authAsAcmeAdmin(page);
  const api = await playwright.request.newContext({
    extraHTTPHeaders: ACME_HEADERS,
  });

  const truck = await firstSeededTruckForTenant(api);
  const event = await firstHighSeverityEventWithClip(api, truck.id);

  // The clip we'll later attach via the modal must belong to the same
  // tenant. We pick *another* clip on the same truck so the "Attach clip"
  // filter (truck dropdown) reliably returns at least one row that isn't
  // already the event's clip. The case-create step already attaches
  // event.clip_id; we want a second, distinct clip.
  const trucksResp = await api.get(
    `${API_BASE}/clips?truck_id=${truck.id}&limit=10`,
  );
  expect(trucksResp.ok()).toBe(true);
  const clipsForTruck = (await trucksResp.json()) as { items: { id: string }[] };
  const additionalClip = clipsForTruck.items.find((c) => c.id !== event.clip_id);
  expect(
    additionalClip,
    `couldn't find a second clip for truck ${truck.id}; need ≥2 for attach scenario`,
  ).toBeTruthy();

  await page.goto(`/trucks/${truck.id}/events`);

  // Open triage menu on the chosen event → "Open case".
  const row = page.getByTestId(`event-row-${event.id}`);
  await expect(row).toBeVisible({ timeout: 10_000 });
  await page.getByTestId(`event-triage-trigger-${event.id}`).click();
  await page.getByTestId(`event-triage-open-case-${event.id}`).click();

  // The OpenCaseModal pops up. The incident_at field is pre-filled from
  // the event's `occurred_at`, so we only need to fill the requester name
  // and submit.
  const modal = page.getByTestId("open-case-modal");
  await expect(modal).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("open-case-requester-name").fill("Test Requester");
  await page.getByTestId("open-case-submit").click();

  // Wait for navigation to /cases/<newId>. Capture the id from the URL.
  await page.waitForURL(/\/cases\/[0-9a-f-]{36}$/, { timeout: 15_000 });
  const url = new URL(page.url());
  const caseId = url.pathname.split("/").pop();
  expect(caseId, "missing caseId in URL").toBeTruthy();

  // Header shows a `C-YYYY-NNNN` number.
  const number = page.getByTestId("case-detail-number");
  await expect(number).toBeVisible();
  await expect(number).toHaveText(/^C-\d{4}-\d{4}$/);

  // Click "Attach clip" → modal opens.
  await page.getByTestId("case-detail-attach").click();
  const attachModal = page.getByTestId("attach-clip-modal");
  await expect(attachModal).toBeVisible({ timeout: 10_000 });

  // Filter by the truck so the result list includes the additional clip.
  // The select's value is the truck id.
  await page.getByTestId("attach-clip-truck").selectOption(truck.id);

  // Click "+" on our extra clip.
  const addButton = page.getByTestId(`attach-clip-add-${additionalClip!.id}`);
  await expect(addButton).toBeVisible({ timeout: 10_000 });
  await addButton.click();

  // Modal closes; the new clip shows in the Evidence list.
  await expect(attachModal).toBeHidden({ timeout: 10_000 });
  await expect(
    page.getByTestId(`case-detail-clip-${additionalClip!.id}`),
  ).toBeVisible({ timeout: 10_000 });

  // Status → under_review.
  await page
    .getByTestId("case-detail-status-select")
    .selectOption("under_review");
  await expect(page.getByTestId("case-status-under_review")).toBeVisible({
    timeout: 10_000,
  });

  // Close with a reason.
  await page.getByTestId("case-detail-close").click();
  const closeModal = page.getByTestId("close-case-modal");
  await expect(closeModal).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("close-case-reason").fill("Smoke test close");
  await page.getByTestId("close-case-submit").click();

  // Header now shows "Closed".
  await expect(page.getByTestId("case-status-closed")).toBeVisible({
    timeout: 10_000,
  });

  // Activity tab lists each step.
  await page.getByTestId("case-detail-tab-activity").click();
  const activity = page.getByTestId("case-detail-activity");
  await expect(activity).toBeVisible({ timeout: 10_000 });

  // The list is rendered newest-first; we just need to confirm each
  // action appears at least once. `getByText` searches descendants.
  for (const action of [
    "case.created",
    "case.clip_attached",
    "case.updated",
    "case.closed",
  ]) {
    await expect(
      activity.getByText(action, { exact: true }).first(),
      `expected activity to include ${action}`,
    ).toBeVisible();
  }

  await api.dispose();
});
