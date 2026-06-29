/**
 * Tests for `<ClipPage />`.
 *
 * Tests jsdom video behaviour by mocking the React Query hooks and
 * driving the page's keyboard handlers + speed buttons via React
 * Testing Library. We don't try to load a real MP4 — jsdom doesn't
 * implement the media element loading path. Instead we mutate the
 * `currentTime` / `playbackRate` properties on the rendered
 * `HTMLVideoElement` directly to assert what the user-facing controls
 * cause to happen.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { ClipDetail, EventRow } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const emitPlay = vi.fn();
const emitScrub = vi.fn();
const emitClosed = vi.fn();

vi.mock("@/hooks/useAuditEmitter", () => ({
  useAuditEmitter: () => ({ emitPlay, emitScrub, emitClosed }),
}));

const fakeDetail: ClipDetail = {
  id: "clip-1",
  tenant_id: "tn",
  truck_id: "t1",
  truck_label: "Freightliner-99",
  driver_id: "d1",
  driver_name: "Alice",
  started_at: "2026-06-29T12:00:00Z",
  ended_at: "2026-06-29T12:01:00Z",
  duration_s: 60,
  storage_key: "tn/2026/06/29/x.mp4",
  ingested_at: "2026-06-29T12:01:30Z",
  sha256: null,
  dashcam_firmware: null,
  playback_url: "https://signed.example.test/x.mp4?sig=test",
};

const fakeEvents: EventRow[] = [
  {
    id: "ev-1",
    tenant_id: "tn",
    truck_id: "t1",
    truck_label: "Freightliner-99",
    driver_id: null,
    driver_name: null,
    clip_id: "clip-1",
    // 10s into the clip.
    occurred_at: "2026-06-29T12:00:10Z",
    type: "harsh_brake",
    severity: "high",
    telemetry: { speed_kmh: 78 },
    gps_lat: null,
    gps_lng: null,
  },
  {
    id: "ev-2",
    tenant_id: "tn",
    truck_id: "t1",
    truck_label: "Freightliner-99",
    driver_id: null,
    driver_name: null,
    clip_id: "clip-1",
    // 30s into the clip.
    occurred_at: "2026-06-29T12:00:30Z",
    type: "collision",
    severity: "critical",
    telemetry: { speed_kmh: 52 },
    gps_lat: null,
    gps_lng: null,
  },
];

let detailQueryState: {
  data: ClipDetail | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
} = {
  data: fakeDetail,
  isLoading: false,
  isError: false,
  error: null,
};
const refetchDetail = vi.fn();

let eventsQueryState: {
  data: { items: EventRow[]; next_cursor: string | null } | undefined;
  isLoading: boolean;
} = { data: { items: fakeEvents, next_cursor: null }, isLoading: false };

vi.mock("@/hooks/useClipDetail", () => ({
  useClipDetail: () => ({ ...detailQueryState, refetch: refetchDetail }),
}));

vi.mock("@/hooks/useClipEvents", () => ({
  useClipEvents: () => ({ ...eventsQueryState }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

import { ClipPage } from "./ClipPage";

function renderPage(path = "/clips/clip-1") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/clips/:id" element={<ClipPage />} />
          <Route path="/search" element={<div>search page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * Replace `currentTime` / `playbackRate` on the video element with
 * writable properties so our test assertions actually observe what the
 * production code sets. jsdom's `HTMLMediaElement` is mostly a stub —
 * by default writes to `currentTime` are silently dropped.
 */
function stubVideoProps(video: HTMLVideoElement) {
  let t = 0;
  let rate = 1;
  Object.defineProperty(video, "currentTime", {
    configurable: true,
    get: () => t,
    set: (next: number) => {
      t = next;
    },
  });
  Object.defineProperty(video, "playbackRate", {
    configurable: true,
    get: () => rate,
    set: (next: number) => {
      rate = next;
    },
  });
  // jsdom's play() rejects without a media source; stub it out.
  Object.defineProperty(video, "paused", {
    configurable: true,
    get: () => true,
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ClipPage", () => {
  beforeEach(() => {
    detailQueryState = {
      data: fakeDetail,
      isLoading: false,
      isError: false,
      error: null,
    };
    eventsQueryState = {
      data: { items: fakeEvents, next_cursor: null },
      isLoading: false,
    };
    emitPlay.mockReset();
    emitScrub.mockReset();
    emitClosed.mockReset();
    refetchDetail.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the video src and truck label from the detail data", () => {
    renderPage();
    const video = screen.getByTestId("clip-video") as HTMLVideoElement;
    // jsdom resolves the src against the document base URL, so compare
    // by tail rather than exact-match.
    expect(video.getAttribute("src")).toContain(
      "https://signed.example.test/x.mp4",
    );
    expect(screen.getByTestId("clip-truck-label")).toHaveTextContent(
      "Freightliner-99",
    );
    expect(screen.getByTestId("clip-duration")).toHaveTextContent("60s");
  });

  it("emits clip.play exactly once on mount", () => {
    renderPage();
    expect(emitPlay).toHaveBeenCalledTimes(1);
  });

  it("advances currentTime by one frame on ArrowRight", () => {
    renderPage();
    const video = screen.getByTestId("clip-video") as HTMLVideoElement;
    stubVideoProps(video);
    video.currentTime = 5;
    fireEvent.keyDown(window, { key: "ArrowRight" });
    // One frame at 30fps ≈ 0.0333…s — we just assert "moved forward".
    expect(video.currentTime).toBeGreaterThan(5);
    expect(video.currentTime).toBeLessThan(5 + 1 / 15);
  });

  it("rewinds currentTime by one frame on ArrowLeft", () => {
    renderPage();
    const video = screen.getByTestId("clip-video") as HTMLVideoElement;
    stubVideoProps(video);
    video.currentTime = 5;
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(video.currentTime).toBeLessThan(5);
    expect(video.currentTime).toBeGreaterThan(5 - 1 / 15);
  });

  it("applies the selected playback speed", () => {
    renderPage();
    const video = screen.getByTestId("clip-video") as HTMLVideoElement;
    stubVideoProps(video);

    fireEvent.click(screen.getByTestId("speed-2x"));
    expect(video.playbackRate).toBe(2);

    fireEvent.click(screen.getByTestId("speed-0.5x"));
    expect(video.playbackRate).toBe(0.5);
  });

  it("seeks the video when an event marker is clicked", () => {
    renderPage();
    const video = screen.getByTestId("clip-video") as HTMLVideoElement;
    stubVideoProps(video);

    // The first event lives 10s into the clip.
    const marker = screen.getByTestId("event-marker-ev-1");
    fireEvent.click(marker);
    expect(video.currentTime).toBeCloseTo(10, 5);

    // The harsh-event list item should also seek when clicked.
    const listItem = screen.getByTestId("event-list-item-ev-2");
    fireEvent.click(listItem);
    expect(video.currentTime).toBeCloseTo(30, 5);
  });

  it("renders one timeline marker per linked event", () => {
    renderPage();
    expect(screen.getByTestId("event-marker-ev-1")).toBeInTheDocument();
    expect(screen.getByTestId("event-marker-ev-2")).toBeInTheDocument();
  });

  it("fires emitClosed with a view_duration_s payload on unmount", () => {
    const { unmount } = renderPage();
    expect(emitClosed).not.toHaveBeenCalled();
    unmount();
    expect(emitClosed).toHaveBeenCalledTimes(1);
    const call = emitClosed.mock.calls[0]?.[0];
    expect(call).toBeDefined();
    expect(call.payload).toMatchObject({
      view_duration_s: expect.any(Number),
    });
  });

  it("emits clip.scrub when the user seeks the video", () => {
    renderPage();
    const video = screen.getByTestId("clip-video") as HTMLVideoElement;
    stubVideoProps(video);
    video.currentTime = 12;
    fireEvent.seeking(video);
    expect(emitScrub).toHaveBeenCalled();
    const lastCall = emitScrub.mock.calls.at(-1)?.[0];
    expect(lastCall?.payload).toMatchObject({ time: 12 });
  });

  it("renders a not-found state when the clip detail 404s", () => {
    detailQueryState = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(404, "not found"),
    };
    renderPage();
    expect(screen.getByTestId("clip-not-found")).toBeInTheDocument();
  });

  it("renders an error state with retry on 500", () => {
    detailQueryState = {
      data: undefined,
      isLoading: false,
      isError: true,
      error: new ApiError(500, "boom"),
    };
    renderPage();
    expect(screen.getByTestId("clip-error")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Retry"));
    expect(refetchDetail).toHaveBeenCalled();
  });
});
