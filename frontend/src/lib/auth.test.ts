import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `auth.ts` reads `IS_DEV` at module init, so we mock the env module
// before the dynamic import inside each test.  Using `vi.mock` is
// awkward with `IS_DEV` because the literal is captured at import time;
// instead we re-import the auth module after stubbing `import.meta.env`.

describe("auth helpers", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("returns X-Dev-* headers when dev mode and selection are set", async () => {
    vi.stubEnv("VITE_APP_ENV", "dev");
    const auth = await import("./auth");
    // Pick the second DEV_USERS entry (viewer) so the test exercises the
    // selection path rather than the default. The self-heal in
    // getDevSelection() will discard arbitrary UUIDs not in DEV_USERS, so
    // a real entry is required here.
    const viewer = auth.DEV_USERS[1];
    auth.setDevUser(viewer.user_id, viewer.tenant_id);

    expect(auth.getDevHeaders()).toEqual({
      "X-Dev-User-Id": viewer.user_id,
      "X-Dev-Tenant-Id": viewer.tenant_id,
    });
    expect(auth.getAuthHeaders()).toEqual({
      "X-Dev-User-Id": viewer.user_id,
      "X-Dev-Tenant-Id": viewer.tenant_id,
    });
  });

  it("falls back to the default dev user when localStorage is empty", async () => {
    vi.stubEnv("VITE_APP_ENV", "dev");
    const auth = await import("./auth");

    const headers = auth.getDevHeaders();
    // The default dev user is the first entry in DEV_USERS (Acme admin).
    expect(headers["X-Dev-User-Id"]).toBe(auth.DEV_USERS[0].user_id);
    expect(headers["X-Dev-Tenant-Id"]).toBe(auth.DEV_USERS[0].tenant_id);
  });

  it("returns no headers in prod mode", async () => {
    vi.stubEnv("VITE_APP_ENV", "prod");
    const auth = await import("./auth");
    auth.setDevUser("u-1", "t-1");

    expect(auth.getDevHeaders()).toEqual({});
    expect(auth.getAuthHeaders()).toEqual({});
  });

  it("setDevUser persists to localStorage and fires a custom event", async () => {
    vi.stubEnv("VITE_APP_ENV", "dev");
    const auth = await import("./auth");

    const handler = vi.fn();
    window.addEventListener("auth:dev-user-changed", handler);

    auth.setDevUser("u-2", "t-2");

    expect(localStorage.getItem("dashcam.dev_user")).toBe("u-2");
    expect(localStorage.getItem("dashcam.dev_tenant")).toBe("t-2");
    expect(handler).toHaveBeenCalledTimes(1);

    window.removeEventListener("auth:dev-user-changed", handler);
  });

  it("self-heals when localStorage holds a user_id that's no longer in DEV_USERS", async () => {
    // Simulate the post-T18b scenario: a browser persisted the old
    // Northwind admin selection before the seed was shrunk to Acme only.
    localStorage.setItem(
      "dashcam.dev_user",
      "35a98177-6209-56fd-b029-d13da11a5ffc",
    );
    localStorage.setItem(
      "dashcam.dev_tenant",
      "9b8edf70-4582-586b-9627-768850412a8b",
    );

    vi.stubEnv("VITE_APP_ENV", "dev");
    const auth = await import("./auth");

    const headers = auth.getDevHeaders();
    // Stale selection should be replaced with the default (Acme admin).
    expect(headers["X-Dev-User-Id"]).toBe(auth.DEV_USERS[0].user_id);
    expect(headers["X-Dev-Tenant-Id"]).toBe(auth.DEV_USERS[0].tenant_id);
    // …and the stale keys should be cleared so we don't reheal every call.
    expect(localStorage.getItem("dashcam.dev_user")).toBeNull();
    expect(localStorage.getItem("dashcam.dev_tenant")).toBeNull();
  });

  it("getTenantName resolves seeded tenant IDs to display names", async () => {
    vi.stubEnv("VITE_APP_ENV", "dev");
    const auth = await import("./auth");
    const acme = auth.DEV_USERS.find((u) => u.tenant_name === "Acme Logistics");
    expect(acme).toBeDefined();
    expect(auth.getTenantName(acme!.tenant_id)).toBe("Acme Logistics");
    expect(auth.getTenantName("unknown")).toBe("Unknown Tenant");
    expect(auth.getTenantName(undefined)).toBe("");
  });
});
