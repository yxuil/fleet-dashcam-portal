/**
 * Typed fetch wrapper for the dashcam portal backend.
 *
 * Responsibilities:
 *   - Prefix relative paths with `API_BASE`.
 *   - Attach auth headers from `auth.ts` on every request.
 *   - Serialize JSON bodies and set the right `Content-Type`.
 *   - Surface non-2xx responses as `ApiError` so React Query's `onError`
 *     paths and the layout-level error boundary see a consistent shape.
 *   - Dispatch a `window` `"auth:unauthorized"` event on 401 so the shell
 *     can prompt for re-auth (in dev, that just nudges the picker).
 */

import { API_BASE } from "./env";
import { getAuthHeaders } from "./auth";

/** Error thrown by `api<T>` for any non-2xx response. */
export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(`HTTP ${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** Custom event name dispatched on every 401 the API wrapper sees. */
export const UNAUTHORIZED_EVENT = "auth:unauthorized";

function buildUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  if (!path.startsWith("/")) return `${API_BASE}/${path}`;
  return `${API_BASE}${path}`;
}

/** True iff `body` looks like an already-serialised string/FormData/Blob. */
function isRawBody(body: unknown): boolean {
  if (body === undefined || body === null) return true;
  if (typeof body === "string") return true;
  if (typeof FormData !== "undefined" && body instanceof FormData) return true;
  if (typeof Blob !== "undefined" && body instanceof Blob) return true;
  if (typeof ArrayBuffer !== "undefined" && body instanceof ArrayBuffer) return true;
  return false;
}

async function parseErrorDetail(res: Response): Promise<string> {
  // The FastAPI default error shape is `{ detail: string | object }`.
  // Fall back to status text for non-JSON responses (e.g. 502 from a
  // reverse proxy that doesn't serialise JSON).
  try {
    const ct = res.headers.get("content-type") ?? "";
    if (ct.includes("application/json")) {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") return body.detail;
      if (body.detail !== undefined) return JSON.stringify(body.detail);
    }
    const text = await res.text();
    if (text) return text;
  } catch {
    // Ignored — fall through to statusText.
  }
  return res.statusText || `status ${res.status}`;
}

/**
 * Issue an HTTP request against the backend and return JSON.
 *
 * `T = void` is allowed for 204-style endpoints (we just return `undefined`).
 */
export async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const url = buildUrl(path);

  // Merge headers carefully so caller-supplied entries win.
  const headers = new Headers(opts.headers);
  for (const [k, v] of Object.entries(getAuthHeaders())) {
    if (!headers.has(k)) headers.set(k, v);
  }

  let body = opts.body;
  if (body !== undefined && !isRawBody(body)) {
    body = JSON.stringify(body);
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  }
  if (!headers.has("Accept")) headers.set("Accept", "application/json");

  const res = await fetch(url, {
    ...opts,
    headers,
    body,
    // In prod the upstream cookie carries auth — keep credentials on so
    // it makes it through. In dev the X-Dev-* headers do the work, but
    // including credentials is harmless.
    credentials: opts.credentials ?? "include",
  });

  if (res.status === 401) {
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
    }
  }

  if (!res.ok) {
    const detail = await parseErrorDetail(res);
    throw new ApiError(res.status, detail);
  }

  // 204 / 205 / explicit no-content responses: don't try to parse JSON.
  if (res.status === 204 || res.status === 205) {
    return undefined as T;
  }
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) {
    // Some endpoints (e.g. presigned URL redirects) might return text.
    const text = await res.text();
    return text as unknown as T;
  }
  return (await res.json()) as T;
}

/** GET helper. */
export function apiGet<T>(path: string, opts: Omit<RequestInit, "method" | "body"> = {}): Promise<T> {
  return api<T>(path, { ...opts, method: "GET" });
}

/** POST helper; `body` is JSON-serialised automatically. */
export function apiPost<T>(
  path: string,
  body?: unknown,
  opts: Omit<RequestInit, "method" | "body"> = {},
): Promise<T> {
  return api<T>(path, { ...opts, method: "POST", body: body as BodyInit | undefined });
}

/** PATCH helper. */
export function apiPatch<T>(
  path: string,
  body?: unknown,
  opts: Omit<RequestInit, "method" | "body"> = {},
): Promise<T> {
  return api<T>(path, { ...opts, method: "PATCH", body: body as BodyInit | undefined });
}

/** DELETE helper; rarely carries a body. */
export function apiDelete<T = void>(
  path: string,
  opts: Omit<RequestInit, "method"> = {},
): Promise<T> {
  return api<T>(path, { ...opts, method: "DELETE" });
}
