/**
 * Browser uploader for `POST /clips/upload`.
 *
 * Why an XHR instead of `fetch` + `useMutation`?
 *   `fetch` can stream a request body for upload-progress only with
 *   `ReadableStream` shenanigans that aren't supported on every browser
 *   yet. `XMLHttpRequest.upload.onprogress` works everywhere and lets us
 *   drive a per-file progress bar in the upload modal cheaply.
 *
 *   We're also not a typical TanStack mutation — the modal manages many
 *   files in flight with its own concurrency loop and rolls up
 *   per-file status. Wrapping each one in `useMutation` would fight
 *   that model.
 *
 * Returns `{ promise, abort }` so the caller can cancel an in-flight
 * upload (e.g. when the user closes the modal mid-batch).
 */

import { API_BASE } from "@/lib/env";
import { ApiError } from "@/lib/api";
import { getAuthHeaders } from "@/lib/auth";
import type { ClipDetail } from "@/lib/types";

export type UploadPayload = {
  file: File;
  truck_id: string;
  driver_id?: string;
  /** ISO 8601 datetime — should match the file's intended `started_at`. */
  started_at: string;
  /** 0 if unknown (e.g. the duration probe timed out). */
  duration_s?: number;
  /** Called with a percentage in [0, 100]. */
  onProgress?: (pct: number) => void;
};

export type UploadHandle = {
  promise: Promise<ClipDetail>;
  abort: () => void;
};

/**
 * Issue one `POST /clips/upload` request.
 *
 * Resolves with the parsed `ClipDetail` on 2xx. Rejects with an
 * {@link ApiError} on any non-2xx response (parsing the FastAPI
 * `{detail}` shape best-effort), or with a generic `Error` if the
 * request was aborted / the network died.
 */
export function uploadClip(payload: UploadPayload): UploadHandle {
  const { file, truck_id, driver_id, started_at, duration_s, onProgress } =
    payload;

  const xhr = new XMLHttpRequest();
  const promise = new Promise<ClipDetail>((resolve, reject) => {
    const form = new FormData();
    form.append("truck_id", truck_id);
    if (driver_id) form.append("driver_id", driver_id);
    form.append("started_at", started_at);
    form.append("duration_s", String(duration_s ?? 0));
    form.append("file", file, file.name);

    xhr.open("POST", `${API_BASE}/clips/upload`, true);
    xhr.withCredentials = true;

    // Attach dev/auth headers — same shape as the `api()` wrapper.
    for (const [k, v] of Object.entries(getAuthHeaders())) {
      xhr.setRequestHeader(k, v);
    }
    xhr.setRequestHeader("Accept", "application/json");
    // Intentionally do NOT set Content-Type — the browser sets the
    // multipart boundary automatically when given a FormData body.

    xhr.upload.onprogress = (e: ProgressEvent<EventTarget>) => {
      if (!onProgress) return;
      if (!e.lengthComputable) return;
      const pct = e.total > 0 ? (e.loaded / e.total) * 100 : 0;
      onProgress(Math.min(100, Math.max(0, pct)));
    };

    xhr.onload = () => {
      // Cover both 2xx and the not-quite-success edge cases.
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const parsed = JSON.parse(xhr.responseText) as ClipDetail;
          resolve(parsed);
        } catch (err) {
          reject(
            new ApiError(xhr.status, "invalid JSON in upload response"),
          );
          // Swallow the parse error reference but keep the stack readable.
          void err;
        }
        return;
      }
      // Best-effort: parse FastAPI's `{detail: "..."}` shape.
      let detail = xhr.statusText || `status ${xhr.status}`;
      try {
        const body = JSON.parse(xhr.responseText) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
        else if (body.detail !== undefined) detail = JSON.stringify(body.detail);
      } catch {
        // Non-JSON error body — fall through to statusText.
      }
      reject(new ApiError(xhr.status, detail));
    };

    xhr.onerror = () => {
      reject(new ApiError(0, "network error"));
    };
    xhr.onabort = () => {
      reject(new ApiError(0, "upload aborted"));
    };

    xhr.send(form);
  });

  return {
    promise,
    abort: () => {
      try {
        xhr.abort();
      } catch {
        // ignore — abort on a settled XHR is a no-op anyway.
      }
    },
  };
}
