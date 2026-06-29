/**
 * Tests for `<AttachClipModal />`.
 *
 * Verifies:
 *   - Renders nothing when closed.
 *   - Renders results once a filter is applied and forwards the chosen
 *     clip id to `onAttach`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/hooks/useTrucks", () => ({
  useTrucks: () => ({
    data: [
      {
        id: "t1",
        tenant_id: "tn",
        label: "Truck-1",
        vin: null,
        dashcam_serial: null,
        last_seen_at: null,
      },
    ],
    isLoading: false,
  }),
}));

vi.mock("@/hooks/useClips", async () => {
  const actual = await vi.importActual<typeof import("@/hooks/useClips")>(
    "@/hooks/useClips",
  );
  return {
    ...actual,
    useClips: () => ({
      data: {
        items: [
          {
            id: "clip-a",
            tenant_id: "tn",
            truck_id: "t1",
            truck_label: "Truck-1",
            driver_id: null,
            driver_name: null,
            started_at: "2026-06-12T14:00:00Z",
            ended_at: "2026-06-12T14:00:30Z",
            duration_s: 30,
            storage_key: "tn/2026/06/12/a.mp4",
            ingested_at: "2026-06-12T14:01:00Z",
          },
          {
            id: "clip-b",
            tenant_id: "tn",
            truck_id: "t1",
            truck_label: "Truck-1",
            driver_id: null,
            driver_name: null,
            started_at: "2026-06-12T15:00:00Z",
            ended_at: "2026-06-12T15:00:30Z",
            duration_s: 30,
            storage_key: "tn/2026/06/12/b.mp4",
            ingested_at: "2026-06-12T15:01:00Z",
          },
        ],
        pages: [],
        pageParams: [],
      },
      isLoading: false,
      isError: false,
    }),
  };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

import { AttachClipModal } from "./AttachClipModal";

function renderModal(props: {
  open: boolean;
  onAttach?: (id: string) => void;
  onClose?: () => void;
}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <AttachClipModal
        open={props.open}
        isSubmitting={false}
        error={null}
        onAttach={props.onAttach ?? (() => {})}
        onClose={props.onClose ?? (() => {})}
      />
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AttachClipModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when closed", () => {
    renderModal({ open: false });
    expect(screen.queryByTestId("attach-clip-modal")).not.toBeInTheDocument();
  });

  it("renders the picker and a hint until a filter is applied", () => {
    renderModal({ open: true });
    expect(screen.getByTestId("attach-clip-modal")).toBeInTheDocument();
    expect(screen.getByTestId("attach-clip-hint")).toBeInTheDocument();
    expect(screen.queryByTestId("attach-clip-results")).not.toBeInTheDocument();
  });

  it("shows results once a truck filter is set and forwards clicks to onAttach", () => {
    const onAttach = vi.fn();
    const onClose = vi.fn();
    renderModal({ open: true, onAttach, onClose });

    fireEvent.change(screen.getByTestId("attach-clip-truck"), {
      target: { value: "t1" },
    });

    expect(screen.getByTestId("attach-clip-results")).toBeInTheDocument();
    expect(screen.getByTestId("attach-clip-row-clip-a")).toBeInTheDocument();
    expect(screen.getByTestId("attach-clip-row-clip-b")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("attach-clip-add-clip-a"));
    expect(onAttach).toHaveBeenCalledWith("clip-a");
  });

  it("Close button calls onClose", () => {
    const onClose = vi.fn();
    renderModal({ open: true, onClose });
    fireEvent.click(screen.getByTestId("attach-clip-cancel"));
    expect(onClose).toHaveBeenCalled();
  });
});
