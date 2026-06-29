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
    auth.setDevUser("u-1", "t-1");

    expect(auth.getDevHeaders()).toEqual({
      "X-Dev-User-Id": "u-1",
      "X-Dev-Tenant-Id": "t-1",
    });
    expect(auth.getAuthHeaders()).toEqual({
      "X-Dev-User-Id": "u-1",
      "X-Dev-Tenant-Id": "t-1",
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
