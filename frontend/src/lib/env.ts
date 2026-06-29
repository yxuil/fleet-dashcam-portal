/**
 * Centralised access to Vite environment variables.
 *
 * Reading `import.meta.env.*` directly inside every module makes mocking
 * painful and scatters string literals; instead, everything funnels
 * through these two constants. Defaults are intentionally generous so
 * `npm run dev` works without a `.env.local`.
 */

const env = import.meta.env as ImportMetaEnv & {
  VITE_API_BASE?: string;
  VITE_APP_ENV?: string;
};

/** Backend base URL (no trailing slash). */
export const API_BASE: string = (env.VITE_API_BASE ?? "http://localhost:8000").replace(
  /\/+$/,
  "",
);

/** Either "dev" or "prod". Drives DevUserPicker visibility and auth mode. */
export const APP_ENV: "dev" | "prod" =
  (env.VITE_APP_ENV as "dev" | "prod" | undefined) ?? "dev";

/** True when running in dev mode — enables localStorage-backed user picker. */
export const IS_DEV: boolean = APP_ENV === "dev";
