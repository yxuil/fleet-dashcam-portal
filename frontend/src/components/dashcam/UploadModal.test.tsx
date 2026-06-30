/**
 * Tests for `<UploadModal />`.
 *
 * Strategy:
 *   - Mock `useTrucks` / `useDrivers` so the dropdowns render
 *     synchronously.
 *   - Mock `uploadClip` so we don't make real network calls; capture
 *     payloads to assert on.
 *   - Drive the *fallback* directory-input path (jsdom doesn't expose
 *     `window.showDirectoryPicker`, so the modal falls back naturally).
 *   - The hidden `<video>` duration probe runs in jsdom but its
 *     `onloadedmetadata` never fires; the 5-second timeout kicks in and
 *     `duration_s` ends up `0`. We pre-empt the wait by skipping the
 *     real timer via fake timers in the upload tests.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ClipDetail } from "@/lib/types";

// ---------------------------------------------------------------------------
// Hook mocks — must come before the component import (vi.mock is hoisted).
// ---------------------------------------------------------------------------

vi.mock("@/hooks/useTrucks", () => ({
  useTrucks: () => ({
    data: [
      {
        id: "t1",
        tenant_id: "tn",
        label: "Truck 101",
        vin: null,
        dashcam_serial: null,
        last_seen_at: null,
      },
      {
        id: "t2",
        tenant_id: "tn",
        label: "Truck 102",
        vin: null,
        dashcam_serial: null,
        last_seen_at: null,
      },
    ],
    isLoading: false,
  }),
}));

vi.mock("@/hooks/useDrivers", () => ({
  useDrivers: () => ({
    data: [{ id: "d1", tenant_id: "tn", name: "Alice", employee_ref: null }],
    isLoading: false,
  }),
}));

// Capture-and-resolve mock for the XHR-based uploader.
const uploadClipMock = vi.fn();
vi.mock("@/hooks/useUploadClip", () => ({
  uploadClip: (...args: unknown[]) => uploadClipMock(...args),
}));

import { UploadModal } from "./UploadModal";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderModal(open = true, onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <UploadModal open={open} onClose={onClose} />
    </QueryClientProvider>,
  );
}

/** Push a fake `FileList` into the hidden directory input. */
function dispatchFiles(files: File[]) {
  const input = screen.getByTestId(
    "upload-modal-fallback-input",
  ) as HTMLInputElement;
  // jsdom doesn't expose `DataTransfer`. Build a minimal FileList-like
  // value directly — the modal only iterates `Array.from(input.files)`,
  // so length + indexed access is enough.
  const fileList: FileList = Object.assign([...files], {
    item: (i: number) => files[i] ?? null,
  }) as unknown as FileList;
  Object.defineProperty(input, "files", {
    value: fileList,
    configurable: true,
  });
  fireEvent.change(input);
}

function makeFile(name: string, size = 1024): File {
  // The contents don't matter — we only ever pass these to a mocked
  // uploader. A small payload keeps jsdom happy.
  return new File([new Uint8Array(size)], name, { type: "video/mp4" });
}

beforeEach(() => {
  uploadClipMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("UploadModal", () => {
  it("renders nothing when closed", () => {
    renderModal(false);
    expect(screen.queryByTestId("upload-modal")).not.toBeInTheDocument();
  });

  it("opens on the source step", () => {
    renderModal();
    expect(screen.getByTestId("upload-modal")).toBeInTheDocument();
    expect(screen.getByTestId("upload-modal-source")).toBeInTheDocument();
  });

  it("after file selection, lists the videos with detected dates", async () => {
    renderModal();
    // Drive fallback input directly (showDirectoryPicker doesn't exist in jsdom).
    const f1 = makeFile("20260628_143012.mp4");
    const f2 = makeFile("random_clip.mp4");
    await act(async () => {
      dispatchFiles([f1, f2]);
    });
    expect(screen.getByTestId("upload-modal-files")).toBeInTheDocument();
    expect(screen.getByText("20260628_143012.mp4")).toBeInTheDocument();
    expect(screen.getByText("random_clip.mp4")).toBeInTheDocument();
    // The recognised filename's date input is pre-populated with the
    // parsed timestamp.
    const dateInputs = screen.getAllByTestId(/upload-modal-row-.*-date/);
    expect(dateInputs.length).toBe(2);
    const recognised = dateInputs.find(
      (el) => (el as HTMLInputElement).value.startsWith("2026-06-28T"),
    );
    expect(recognised).toBeTruthy();
  });

  it("disables Start until a truck is picked", async () => {
    renderModal();
    const f1 = makeFile("20260628_143012.mp4");
    await act(async () => {
      dispatchFiles([f1]);
    });
    const start = screen.getByTestId("upload-modal-start") as HTMLButtonElement;
    expect(start.disabled).toBe(true);

    // Picking a truck flips the button.
    await userEvent.selectOptions(
      screen.getByTestId("upload-modal-truck"),
      "t1",
    );
    expect(
      (screen.getByTestId("upload-modal-start") as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("uploads each selected file with the assigned truck + driver", async () => {
    // Resolve every upload immediately with a synthesised ClipDetail.
    const resolvers: Array<(c: ClipDetail) => void> = [];
    uploadClipMock.mockImplementation((payload: { file: File }) => ({
      promise: new Promise<ClipDetail>((resolve) => {
        resolvers.push(resolve);
      }).then((d) => {
        // Tag the resolved object with the file name so tests can
        // double-check ordering if needed.
        void payload;
        return d;
      }),
      abort: () => {},
    }));

    // Use fake timers so the duration probe's 5-second fallback
    // resolves instantly when we advance.
    vi.useFakeTimers({ shouldAdvanceTime: true });

    renderModal();
    const f1 = makeFile("20260628_143012.mp4");
    const f2 = makeFile("20260628_150500.mp4");
    await act(async () => {
      dispatchFiles([f1, f2]);
    });

    await userEvent.selectOptions(
      screen.getByTestId("upload-modal-truck"),
      "t1",
    );
    await userEvent.selectOptions(
      screen.getByTestId("upload-modal-driver"),
      "d1",
    );

    await act(async () => {
      (screen.getByTestId("upload-modal-start") as HTMLButtonElement).click();
    });

    // Advance through the 5s duration-probe timeout so the upload calls fire.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });

    expect(uploadClipMock).toHaveBeenCalled();
    const calls = uploadClipMock.mock.calls;
    for (const call of calls) {
      const payload = call[0] as {
        truck_id: string;
        driver_id?: string;
        file: File;
        started_at: string;
      };
      expect(payload.truck_id).toBe("t1");
      expect(payload.driver_id).toBe("d1");
      expect(payload.started_at).toMatch(/^\d{4}-/);
    }

    // Resolve both uploads — modal should advance to "done".
    await act(async () => {
      resolvers.forEach((r, i) =>
        r({
          id: `clip-${i}`,
          tenant_id: "tn",
          truck_id: "t1",
          truck_label: "Truck 101",
          driver_id: "d1",
          driver_name: "Alice",
          started_at: "2026-06-28T14:30:12Z",
          ended_at: "2026-06-28T14:30:12Z",
          duration_s: 0,
          storage_key: "tn/2026/06/28/x.mp4",
          ingested_at: "2026-06-28T14:30:12Z",
          sha256: null,
          dashcam_firmware: null,
          playback_url: null,
        }),
      );
    });

    await waitFor(() => {
      expect(screen.getByTestId("upload-modal-done")).toBeInTheDocument();
    });
    expect(screen.getByText(/uploaded/i)).toBeInTheDocument();
  });
});
