/**
 * Dev-mode authentication helpers for the dashcam portal frontend.
 *
 * In dev (`VITE_APP_ENV=dev`) we don't bother minting JWTs in the browser.
 * The backend's `current_user` dependency accepts `X-Dev-User-Id` +
 * `X-Dev-Tenant-Id` headers when `APP_ENV=dev` (see `backend/app/auth.py`),
 * which is plenty for local development.
 *
 * In prod the upstream reverse proxy is expected to inject the
 * `Authorization: Bearer <jwt>` header on every request (cookie → header
 * exchange happens at the edge), so the frontend can just leave headers
 * empty — `credentials: "include"` plus the upstream's cookie carry it.
 *
 * Dev user / tenant IDs are derived from `backend/app/seed.py` —
 * `uuid5(NAMESPACE_DNS, "<slug>.dashcam")` for tenants and
 * `uuid5(NAMESPACE_DNS, "<slug>.user.<role>")` for users.  Mirroring the
 * Python recipe in TS isn't worth a dependency; we just hardcode the
 * computed values and re-derive them via a script if the recipe changes.
 */

import { IS_DEV } from "./env";

/** Minimal authenticated-user shape returned by `GET /me`. */
export type Principal = {
  user_id: string;
  tenant_id: string;
  roles: string[];
  email: string;
  name: string;
};

/** A dev-only user option shown in the corner picker. */
export type DevUser = {
  /** Stable key — used in dropdowns. */
  id: string;
  /** Human label, e.g. "Acme — Admin". */
  label: string;
  /** UUID matching the seeded `users.id` row. */
  user_id: string;
  /** UUID matching the seeded `tenants.id` row. */
  tenant_id: string;
  /** Tenant display name — used by the top bar. */
  tenant_name: string;
  /** Email pulled from the seed (not currently used in dev path). */
  email: string;
  /** Cosmetic name for the topbar; `/me` returns "Dev User" in dev. */
  display_name: string;
};

// ---------------------------------------------------------------------------
// Hardcoded dev IDs (derived from backend/app/seed.py).
//
// Re-derive with:
//   python3 -c "import uuid; D=uuid.NAMESPACE_DNS; \
//     [print(s, uuid.uuid5(D, f'{s}.dashcam'), \
//                  uuid.uuid5(D, f'{s}.user.admin'), \
//                  uuid.uuid5(D, f'{s}.user.viewer')) \
//      for s in ('acme',)]"
//
// The demo seed currently uses a single tenant (Acme). When adding more
// tenants in seed.py, extend DEV_USERS in lockstep.
// ---------------------------------------------------------------------------

export const DEV_USERS: readonly DevUser[] = [
  {
    id: "acme-admin",
    label: "Acme — Admin",
    user_id: "559ab941-158d-5667-a214-ef00157e6375",
    tenant_id: "a6190065-514d-5d27-b599-e81673beb843",
    tenant_name: "Acme Logistics",
    email: "admin@acme.dev",
    display_name: "Acme Logistics Admin",
  },
  {
    id: "acme-viewer",
    label: "Acme — Viewer",
    user_id: "bd1d1049-349e-5d10-90d6-ebbaeabe8780",
    tenant_id: "a6190065-514d-5d27-b599-e81673beb843",
    tenant_name: "Acme Logistics",
    email: "viewer@acme.dev",
    display_name: "Acme Logistics Viewer",
  },
] as const;

const STORAGE_USER_KEY = "dashcam.dev_user";
const STORAGE_TENANT_KEY = "dashcam.dev_tenant";

/** Default to the first Acme admin so a fresh browser still hits `/me`. */
const DEFAULT_DEV_USER = DEV_USERS[0];

function safeLocalStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    // Some sandboxed environments (older Safari private mode, SSR shims)
    // throw on first access. Treat that as "no storage available".
    return null;
  }
}

/** Read the active dev user from localStorage, with a sane default.
 *
 * Self-heals stale selections: if the stored `user_id` no longer matches
 * any entry in `DEV_USERS` (e.g. the seed was simplified and a tenant
 * was dropped), we clear the stale keys and fall back to the default.
 * Without this, a browser that selected Northwind before the shrink to
 * a single-tenant demo would keep sending dev headers for a tenant
 * with zero seeded data, silently rendering an empty Fleet Cam page.
 */
export function getDevSelection(): { user_id: string; tenant_id: string } {
  const ls = safeLocalStorage();
  const stored_user_id = ls?.getItem(STORAGE_USER_KEY) ?? null;
  const stored_tenant_id = ls?.getItem(STORAGE_TENANT_KEY) ?? null;

  const isStaleSelection =
    stored_user_id !== null &&
    !DEV_USERS.some((u) => u.user_id === stored_user_id);
  if (isStaleSelection) {
    ls?.removeItem(STORAGE_USER_KEY);
    ls?.removeItem(STORAGE_TENANT_KEY);
    return {
      user_id: DEFAULT_DEV_USER.user_id,
      tenant_id: DEFAULT_DEV_USER.tenant_id,
    };
  }

  return {
    user_id: stored_user_id ?? DEFAULT_DEV_USER.user_id,
    tenant_id: stored_tenant_id ?? DEFAULT_DEV_USER.tenant_id,
  };
}

/**
 * Return `X-Dev-User-Id` / `X-Dev-Tenant-Id` headers for the backend's
 * dev path. Returns an empty object in prod or when localStorage is
 * unavailable.
 */
export function getDevHeaders(): Record<string, string> {
  if (!IS_DEV) return {};
  const { user_id, tenant_id } = getDevSelection();
  if (!user_id || !tenant_id) return {};
  return {
    "X-Dev-User-Id": user_id,
    "X-Dev-Tenant-Id": tenant_id,
  };
}

/**
 * Headers to attach to every API call.
 *
 * In dev we attach `X-Dev-*` headers. In prod the upstream proxy injects
 * the `Authorization` header so we deliberately return an empty object —
 * `credentials: "include"` on the fetch picks up the cookie that gets
 * exchanged into a bearer token at the edge.
 */
export function getAuthHeaders(): Record<string, string> {
  if (IS_DEV) return getDevHeaders();
  return {};
}

/**
 * Persist a new dev user selection and notify other tabs / listeners.
 * Most callers will follow this with `window.location.reload()` so that
 * React Query refetches everything against the new principal.
 */
export function setDevUser(user_id: string, tenant_id: string): void {
  const ls = safeLocalStorage();
  if (!ls) return;
  ls.setItem(STORAGE_USER_KEY, user_id);
  ls.setItem(STORAGE_TENANT_KEY, tenant_id);

  // `storage` events only fire across tabs, not in the writing tab; emit
  // a custom event so the in-page DevUserPicker can react if it ever
  // wants to (currently it just reloads).
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("auth:dev-user-changed"));
  }
}

/** Look up the rich `DevUser` matching the current dev selection. */
export function getActiveDevUser(): DevUser | null {
  if (!IS_DEV) return null;
  const { user_id } = getDevSelection();
  return DEV_USERS.find((u) => u.user_id === user_id) ?? null;
}

/**
 * Find the tenant display name for a tenant_id. T10 ships a hardcoded
 * map from seed-time tenants; later tasks can swap this for a real
 * `GET /tenants/me` lookup.
 */
export function getTenantName(tenant_id: string | undefined): string {
  if (!tenant_id) return "";
  const match = DEV_USERS.find((u) => u.tenant_id === tenant_id);
  return match?.tenant_name ?? "Unknown Tenant";
}
