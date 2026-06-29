/**
 * Player-lifecycle audit emitter.
 *
 * Wraps `POST /clips/{id}/audit` so the video player can record
 * `clip.play`, `clip.scrub`, and `clip.closed` events. Scrub is
 * debounced client-side to once per 750ms — the user dragging the
 * scrubber would otherwise emit hundreds of audit rows per minute,
 * which is both noisy in the log and a lot of pointless network traffic.
 *
 * The hook intentionally does NOT use React Query's `useMutation`: the
 * emitter is fire-and-forget (errors are logged, not surfaced to the
 * UI) and we want stable identity so we can wire it into refs and
 * `useEffect` cleanup without dependency churn.
 */

import { useCallback, useEffect, useRef } from "react";

import { apiPost } from "@/lib/api";

/** Allowed action values — must match the backend's closed set. */
export type ClipAuditAction = "clip.play" | "clip.scrub" | "clip.closed";

/** Debounce window for scrub emissions, in milliseconds. */
const SCRUB_DEBOUNCE_MS = 750;

type EmitOpts = { payload?: Record<string, unknown> };

export type AuditEmitter = {
  emitPlay: (opts?: EmitOpts) => void;
  emitScrub: (opts?: EmitOpts) => void;
  emitClosed: (opts?: EmitOpts) => void;
};

/** Fire an audit POST without blocking the caller; log on failure. */
function postIgnoreErrors(
  clipId: string,
  action: ClipAuditAction,
  payload: Record<string, unknown> | undefined,
): void {
  apiPost<void>(`/clips/${clipId}/audit`, {
    action,
    payload: payload ?? {},
  }).catch((err: unknown) => {
    // The audit endpoint failing shouldn't break playback — we only
    // log so devs notice during local testing.
    // eslint-disable-next-line no-console
    console.warn(`[audit] ${action} for clip ${clipId} failed`, err);
  });
}

export function useAuditEmitter(clipId: string | undefined): AuditEmitter {
  // Stash the latest scrub payload behind a ref so the debounced
  // flusher always sends the *most recent* time, not whatever was
  // queued first.
  const pendingScrubPayload = useRef<Record<string, unknown> | undefined>(undefined);
  const scrubTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Make sure a pending debounce timer doesn't fire after the page is
  // gone — that would leak a closure over an unmounted component.
  useEffect(() => {
    return () => {
      if (scrubTimer.current !== null) {
        clearTimeout(scrubTimer.current);
        scrubTimer.current = null;
      }
    };
  }, []);

  const emitPlay = useCallback(
    (opts?: EmitOpts) => {
      if (!clipId) return;
      postIgnoreErrors(clipId, "clip.play", opts?.payload);
    },
    [clipId],
  );

  const emitScrub = useCallback(
    (opts?: EmitOpts) => {
      if (!clipId) return;
      pendingScrubPayload.current = opts?.payload;
      if (scrubTimer.current !== null) return; // already scheduled
      scrubTimer.current = setTimeout(() => {
        scrubTimer.current = null;
        const payload = pendingScrubPayload.current;
        pendingScrubPayload.current = undefined;
        postIgnoreErrors(clipId, "clip.scrub", payload);
      }, SCRUB_DEBOUNCE_MS);
    },
    [clipId],
  );

  const emitClosed = useCallback(
    (opts?: EmitOpts) => {
      if (!clipId) return;
      // Flush any pending scrub *before* the close emission so the log
      // reads chronologically.
      if (scrubTimer.current !== null) {
        clearTimeout(scrubTimer.current);
        scrubTimer.current = null;
        const payload = pendingScrubPayload.current;
        pendingScrubPayload.current = undefined;
        postIgnoreErrors(clipId, "clip.scrub", payload);
      }
      postIgnoreErrors(clipId, "clip.closed", opts?.payload);
    },
    [clipId],
  );

  return { emitPlay, emitScrub, emitClosed };
}
