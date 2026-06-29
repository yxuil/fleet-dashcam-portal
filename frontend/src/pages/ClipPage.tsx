/**
 * Clip detail / playback page — `/clips/:id`.
 *
 * Loads metadata + a freshly-minted signed playback URL via
 * `useClipDetail` (which calls `GET /clips/{id}?play=true`), then
 * renders a native `<video>` element alongside a telemetry / event
 * timeline panel.
 *
 * Player controls:
 *   - HTML5 built-ins (play/pause/seek/volume).
 *   - Frame-step on `←`/`→` (≈ one 30fps frame each).
 *   - Spacebar toggles play/pause.
 *   - Speed buttons set `video.playbackRate` (0.5x, 1x, 1.5x, 2x, 4x).
 *
 * Telemetry overlay:
 *   - Speed read-out picks the event nearest the current timestamp and
 *     pulls `telemetry.speed_kmh` (if present) from it.
 *   - Harsh-event markers sit above the scrubber, positioned by
 *     `(event.occurred_at - clip.started_at) / duration_s`. Click to
 *     jump; hover for type + severity.
 *
 * Audit events:
 *   - `clip.play` once on mount (the GET already wrote
 *     `clip.play_url_minted`; this records the actual playback start).
 *   - `clip.scrub` on `seeking`/`seeked`, debounced to once per 750ms.
 *   - `clip.closed` on unmount/`beforeunload`, with `view_duration_s`
 *     accumulated while the video was actually playing.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { useAuditEmitter } from "@/hooks/useAuditEmitter";
import { useClipDetail } from "@/hooks/useClipDetail";
import { useClipEvents } from "@/hooks/useClipEvents";
import { ApiError } from "@/lib/api";
import type { ClipDetail, EventRow } from "@/lib/types";
import { cn } from "@/lib/utils";

// One 30fps frame, in seconds. Used by the ←/→ frame-step keybinds.
const FRAME_STEP_S = 1 / 30;

// Speed selector — `null` means "not selected"; we default to 1.
const PLAYBACK_SPEEDS = [0.5, 1, 1.5, 2, 4] as const;
type PlaybackSpeed = (typeof PLAYBACK_SPEEDS)[number];

// Severity → ring colour for the timeline markers.
const SEVERITY_COLOURS: Record<EventRow["severity"], string> = {
  critical: "bg-red-500 border-red-700",
  high: "bg-orange-500 border-orange-700",
  medium: "bg-amber-400 border-amber-600",
  low: "bg-slate-400 border-slate-600",
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function ClipPage() {
  const { id } = useParams<{ id: string }>();
  const clipQuery = useClipDetail(id);
  const eventsQuery = useClipEvents(id);
  const audit = useAuditEmitter(id);

  if (clipQuery.isLoading) {
    return <LoadingState />;
  }

  if (clipQuery.isError) {
    const err = clipQuery.error;
    const is404 = err instanceof ApiError && err.status === 404;
    if (is404) {
      return <NotFoundState />;
    }
    return (
      <ErrorState
        message="Couldn’t load clip"
        status={err instanceof ApiError ? err.status : undefined}
        onRetry={() => clipQuery.refetch()}
      />
    );
  }

  const detail = clipQuery.data;
  if (!detail) {
    return <NotFoundState />;
  }

  return (
    <ClipPageBody
      detail={detail}
      events={eventsQuery.data?.items ?? []}
      audit={audit}
    />
  );
}

// ---------------------------------------------------------------------------
// Main body — split so the hooks don't run until we actually have data.
// ---------------------------------------------------------------------------

type BodyProps = {
  detail: ClipDetail;
  events: EventRow[];
  audit: ReturnType<typeof useAuditEmitter>;
};

function ClipPageBody({ detail, events, audit }: BodyProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [speed, setSpeed] = useState<PlaybackSpeed>(1);
  const [currentTime, setCurrentTime] = useState<number>(0);

  // -----------------------------------------------------------------
  // View-duration accumulator: tick at ~4Hz while the video plays.
  // -----------------------------------------------------------------
  const viewDurationS = useRef(0);
  const lastTick = useRef<number | null>(null);

  useEffect(() => {
    const tickIntervalMs = 250;
    const timer = window.setInterval(() => {
      const v = videoRef.current;
      const now = performance.now();
      if (v && !v.paused && !v.ended) {
        if (lastTick.current !== null) {
          viewDurationS.current += (now - lastTick.current) / 1000;
        }
        lastTick.current = now;
      } else {
        lastTick.current = null;
      }
    }, tickIntervalMs);
    return () => window.clearInterval(timer);
  }, []);

  // -----------------------------------------------------------------
  // Audit lifecycle: clip.play on mount, clip.closed on unmount/unload.
  // -----------------------------------------------------------------
  useEffect(() => {
    audit.emitPlay();
    function onBeforeUnload() {
      audit.emitClosed({
        payload: { view_duration_s: Math.round(viewDurationS.current) },
      });
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      audit.emitClosed({
        payload: { view_duration_s: Math.round(viewDurationS.current) },
      });
    };
    // We intentionally fire emitPlay exactly once per page open. The
    // emitter is stable (memoised on clipId), so the empty-deps disable
    // is safe: rebinding wouldn't change which clip we're auditing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // -----------------------------------------------------------------
  // Keyboard handlers — ←/→ frame-step, space play/pause.
  // -----------------------------------------------------------------
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      const v = videoRef.current;
      if (!v) return;
      // Don't hijack arrow keys from form inputs.
      const target = ev.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (ev.key === "ArrowLeft") {
        ev.preventDefault();
        v.currentTime = Math.max(0, v.currentTime - FRAME_STEP_S);
      } else if (ev.key === "ArrowRight") {
        ev.preventDefault();
        v.currentTime = Math.min(detail.duration_s, v.currentTime + FRAME_STEP_S);
      } else if (ev.key === " " || ev.code === "Space") {
        ev.preventDefault();
        if (v.paused) {
          void v.play();
        } else {
          v.pause();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detail.duration_s]);

  // -----------------------------------------------------------------
  // Speed selector — apply to the video element when it changes.
  // -----------------------------------------------------------------
  useEffect(() => {
    const v = videoRef.current;
    if (v) v.playbackRate = speed;
  }, [speed]);

  // -----------------------------------------------------------------
  // Seek helpers shared between the timeline + harsh-event list.
  // -----------------------------------------------------------------
  const seekTo = useCallback(
    (timeS: number) => {
      const v = videoRef.current;
      if (!v) return;
      const clamped = Math.max(0, Math.min(detail.duration_s, timeS));
      v.currentTime = clamped;
    },
    [detail.duration_s],
  );

  const clipStartedAt = useMemo(
    () => new Date(detail.started_at).getTime(),
    [detail.started_at],
  );

  // Translate an event timestamp into a relative offset within the
  // clip. Out-of-range events are filtered upstream of the marker bar.
  const eventOffsets = useMemo(
    () =>
      events
        .map((ev) => {
          const occurred = new Date(ev.occurred_at).getTime();
          const offsetS = (occurred - clipStartedAt) / 1000;
          return { ev, offsetS };
        })
        .filter(
          (x) =>
            Number.isFinite(x.offsetS) &&
            x.offsetS >= 0 &&
            x.offsetS <= detail.duration_s,
        ),
    [events, clipStartedAt, detail.duration_s],
  );

  // Current speed read-out: pick the event closest to `currentTime`
  // (within ±2s), and pull `telemetry.speed_kmh` from it.
  const currentSpeedKmh = useMemo(() => {
    if (eventOffsets.length === 0) return null;
    const WINDOW_S = 2;
    let best: { offsetS: number; ev: EventRow } | null = null;
    let bestDist = Infinity;
    for (const candidate of eventOffsets) {
      const dist = Math.abs(candidate.offsetS - currentTime);
      if (dist < bestDist && dist <= WINDOW_S) {
        best = candidate;
        bestDist = dist;
      }
    }
    if (!best) return null;
    const raw = best.ev.telemetry.speed_kmh;
    return typeof raw === "number" ? raw : null;
  }, [eventOffsets, currentTime]);

  // -----------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------
  return (
    <section className="space-y-6">
      <Header detail={detail} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[2fr_1fr]">
        <div className="space-y-3">
          <video
            ref={videoRef}
            controls
            preload="metadata"
            src={detail.playback_url ?? undefined}
            data-testid="clip-video"
            className="aspect-video w-full rounded-md bg-black"
            onTimeUpdate={(e) =>
              setCurrentTime((e.target as HTMLVideoElement).currentTime)
            }
            onSeeking={(e) =>
              audit.emitScrub({
                payload: {
                  time: (e.target as HTMLVideoElement).currentTime,
                },
              })
            }
            onSeeked={(e) =>
              audit.emitScrub({
                payload: {
                  time: (e.target as HTMLVideoElement).currentTime,
                },
              })
            }
          >
            <track kind="captions" />
          </video>

          <EventTimeline
            durationS={detail.duration_s}
            events={eventOffsets}
            currentTime={currentTime}
            onJump={seekTo}
          />

          <PlayerControls speed={speed} onSpeedChange={setSpeed} />
        </div>

        <TelemetryPanel
          speedKmh={currentSpeedKmh}
          events={eventOffsets}
          onJump={seekTo}
        />
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function Header({ detail }: { detail: ClipDetail }) {
  return (
    <header className="space-y-1">
      <Link
        to="/search"
        className="text-xs text-muted-foreground underline-offset-2 hover:underline"
        data-testid="back-to-search"
      >
        ← Back to search
      </Link>
      <div className="flex items-baseline justify-between">
        <h1
          className="text-2xl font-semibold tracking-tight"
          data-testid="clip-truck-label"
        >
          {detail.truck_label}
        </h1>
        <p className="text-xs text-muted-foreground">
          <span data-testid="clip-started-at">
            {formatStartedAt(detail.started_at)}
          </span>
          <span aria-hidden> · </span>
          <span data-testid="clip-duration">{detail.duration_s}s</span>
        </p>
      </div>
    </header>
  );
}

type PlayerControlsProps = {
  speed: PlaybackSpeed;
  onSpeedChange: (s: PlaybackSpeed) => void;
};

function PlayerControls({ speed, onSpeedChange }: PlayerControlsProps) {
  return (
    <div
      className="flex items-center gap-2"
      data-testid="player-controls"
    >
      <span className="text-xs text-muted-foreground">Speed</span>
      {PLAYBACK_SPEEDS.map((s) => (
        <Button
          key={s}
          size="sm"
          variant={s === speed ? "default" : "outline"}
          onClick={() => onSpeedChange(s)}
          data-testid={`speed-${s}x`}
        >
          {s}x
        </Button>
      ))}
      <span className="ml-auto text-[10px] text-muted-foreground">
        ←/→ frame-step · Space play/pause
      </span>
    </div>
  );
}

type TimelineProps = {
  durationS: number;
  events: { ev: EventRow; offsetS: number }[];
  currentTime: number;
  onJump: (timeS: number) => void;
};

function EventTimeline({ durationS, events, currentTime, onJump }: TimelineProps) {
  const safeDuration = durationS > 0 ? durationS : 1;
  return (
    <div className="relative h-6 w-full" data-testid="event-timeline">
      <div className="absolute inset-x-0 top-3 h-px bg-border" />
      {/* Current-time tick — purely visual; the user still uses the
          native scrubber to seek. */}
      <div
        className="absolute top-1 h-4 w-px bg-foreground/60"
        style={{ left: `${(currentTime / safeDuration) * 100}%` }}
        aria-hidden
        data-testid="timeline-cursor"
      />
      {events.map(({ ev, offsetS }) => {
        const pct = (offsetS / safeDuration) * 100;
        return (
          <button
            key={ev.id}
            type="button"
            title={`${ev.type} (${ev.severity}) @ ${offsetS.toFixed(1)}s`}
            onClick={() => onJump(offsetS)}
            data-testid={`event-marker-${ev.id}`}
            data-event-offset={offsetS}
            className={cn(
              "absolute top-0 h-4 w-4 -translate-x-1/2 rounded-full border focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              SEVERITY_COLOURS[ev.severity],
            )}
            style={{ left: `${pct}%` }}
          />
        );
      })}
    </div>
  );
}

type TelemetryPanelProps = {
  speedKmh: number | null;
  events: { ev: EventRow; offsetS: number }[];
  onJump: (timeS: number) => void;
};

function TelemetryPanel({ speedKmh, events, onJump }: TelemetryPanelProps) {
  return (
    <aside
      className="space-y-4 rounded-md border border-border bg-card p-4"
      data-testid="telemetry-panel"
    >
      <div>
        <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Speed
        </h2>
        <p className="text-2xl font-semibold" data-testid="telemetry-speed">
          {speedKmh != null ? `${speedKmh.toFixed(0)} km/h` : "—"}
        </p>
      </div>

      <div>
        <h2 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Events
        </h2>
        {events.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">No harsh events.</p>
        ) : (
          <ul className="mt-2 space-y-1" data-testid="event-list">
            {events.map(({ ev, offsetS }) => (
              <li key={ev.id}>
                <button
                  type="button"
                  onClick={() => onJump(offsetS)}
                  data-testid={`event-list-item-${ev.id}`}
                  className="flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left text-sm hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="flex items-center gap-2">
                    <span
                      className={cn(
                        "inline-block h-2 w-2 rounded-full border",
                        SEVERITY_COLOURS[ev.severity],
                      )}
                      aria-hidden
                    />
                    <span>{formatEventType(ev.type)}</span>
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {offsetS.toFixed(1)}s
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Loading / error states
// ---------------------------------------------------------------------------

function LoadingState() {
  return (
    <div className="space-y-4" data-testid="clip-loading">
      <div className="h-4 w-32 animate-pulse rounded bg-muted" />
      <div className="aspect-video w-full max-w-3xl animate-pulse rounded-md bg-muted" />
    </div>
  );
}

function NotFoundState() {
  return (
    <div className="space-y-4" data-testid="clip-not-found">
      <p className="text-lg font-medium">Clip not found.</p>
      <Link
        to="/search"
        className="text-sm text-primary underline-offset-2 hover:underline"
      >
        ← Back to search
      </Link>
    </div>
  );
}

function ErrorState({
  message,
  status,
  onRetry,
}: {
  message: string;
  status?: number;
  onRetry: () => void;
}) {
  const suffix = status != null ? ` (${status})` : "";
  return (
    <div
      role="alert"
      data-testid="clip-error"
      className="flex flex-col items-start gap-2 rounded-md border border-destructive/50 bg-destructive/5 p-4 text-sm"
    >
      <p className="font-medium text-destructive">
        {message}
        {suffix}.
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function formatStartedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

function formatEventType(t: EventRow["type"]): string {
  // "harsh_brake" → "Harsh brake"
  const lower = t.replace(/_/g, " ");
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}
