/**
 * Multi-step upload modal for `POST /clips/upload`.
 *
 * Flow:
 *   1. **source** — pick a folder. Tries `window.showDirectoryPicker`
 *      first (Chromium); falls back to a hidden
 *      `<input type="file" webkitdirectory multiple>` everywhere else
 *      (Safari, Firefox, jsdom).
 *   2. **files** — show the discovered MP4/MOV/MKV/M4V files with
 *      auto-detected dates (editable). Required truck + optional driver
 *      apply to the whole batch.
 *   3. **upload** — fire `POST /clips/upload` per file with concurrency
 *      = 2; render a progress bar each. Failures don't stop the batch.
 *   4. **done** — summary with the per-file outcomes.
 *
 * On close (Cancel / ✕ / Done) we invalidate the React Query keys that
 * feed the truck-day cards so any successful uploads appear immediately
 * on the page behind the modal.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/Button";
import { useDrivers } from "@/hooks/useDrivers";
import { useTrucks } from "@/hooks/useTrucks";
import { uploadClip } from "@/hooks/useUploadClip";
import { ApiError } from "@/lib/api";
import type { ClipDetail, DriverOut, TruckOut } from "@/lib/types";
import { parseDashcamFilenameDate } from "@/lib/uploadDate";
import { cn } from "@/lib/utils";

/** Two parallel uploads — enough to overlap network/storage without thrashing. */
const UPLOAD_CONCURRENCY = 2;
/** Cap on files per batch so the table stays usable. */
const MAX_FILES_PER_BATCH = 50;
/** Recognised video file extensions (case-insensitive). */
const VIDEO_EXT_RE = /\.(mp4|mov|mkv|m4v)$/i;
/** Bail out of `<video>` duration probing after this many ms. */
const DURATION_PROBE_TIMEOUT_MS = 5000;

type Step = "source" | "files" | "upload" | "done";

type FileEntry = {
  /** Stable key separate from `file.name` for React lists. */
  id: string;
  file: File;
  /** Editable local-time datetime in `YYYY-MM-DDTHH:mm` shape. */
  startedAtLocal: string;
  /** Default true; user can untick to exclude a file from the batch. */
  selected: boolean;
};

type UploadStatus =
  | { kind: "pending" }
  | { kind: "uploading"; pct: number }
  | { kind: "done"; clipId: string }
  | { kind: "failed"; error: string };

type UploadRow = {
  id: string;
  file: File;
  startedAt: Date;
  status: UploadStatus;
};

export type UploadModalProps = {
  open: boolean;
  onClose: () => void;
};

export function UploadModal({ open, onClose }: UploadModalProps) {
  const titleId = useId();
  const [step, setStep] = useState<Step>("source");
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [truckId, setTruckId] = useState<string>("");
  const [driverId, setDriverId] = useState<string>("");
  const [overflow, setOverflow] = useState<boolean>(false);
  const [uploadRows, setUploadRows] = useState<UploadRow[]>([]);
  const hasSucceededRef = useRef(false);
  const fallbackInputRef = useRef<HTMLInputElement | null>(null);
  const qc = useQueryClient();

  const trucks = useTrucks();
  const drivers = useDrivers();

  // Reset everything when the modal re-opens — a stale half-finished
  // batch shouldn't persist between sessions.
  useEffect(() => {
    if (open) {
      setStep("source");
      setEntries([]);
      setTruckId("");
      setDriverId("");
      setOverflow(false);
      setUploadRows([]);
      hasSucceededRef.current = false;
    }
  }, [open]);

  // Esc-to-close while we're not actively uploading.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && step !== "upload") handleClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, step]);

  const handleClose = useCallback(() => {
    if (hasSucceededRef.current) {
      // Broad invalidation: trigger every truck-row's day cards to refetch.
      qc.invalidateQueries({ queryKey: ["trucks"] });
      qc.invalidateQueries({ queryKey: ["truck-days"] });
    }
    onClose();
  }, [onClose, qc]);

  const handleFilesPicked = useCallback(async (files: File[]) => {
    const videos = files.filter((f) => VIDEO_EXT_RE.test(f.name));
    const capped = videos.slice(0, MAX_FILES_PER_BATCH);
    setOverflow(videos.length > MAX_FILES_PER_BATCH);
    const next: FileEntry[] = capped.map((file, i) => {
      const detected =
        parseDashcamFilenameDate(file.name) ?? new Date(file.lastModified);
      return {
        id: `${file.name}-${i}-${file.size}`,
        file,
        startedAtLocal: toDatetimeLocal(detected),
        selected: true,
      };
    });
    setEntries(next);
    setStep("files");
  }, []);

  const openDirectoryPicker = useCallback(async () => {
    // File System Access API — Chromium. Wrap in try/catch because
    // (a) it's not present in jsdom; (b) the user can cancel; (c) some
    // platforms throw NotAllowedError on insecure contexts.
    const w = window as unknown as {
      showDirectoryPicker?: (opts?: object) => Promise<unknown>;
    };
    if (typeof w.showDirectoryPicker === "function") {
      try {
        const handle = await w.showDirectoryPicker({ mode: "read" });
        const collected: File[] = [];
        await walkDirectoryHandle(
          handle,
          collected,
          MAX_FILES_PER_BATCH + 1, // collect one extra so we know we overflowed
        );
        await handleFilesPicked(collected);
        return;
      } catch (err) {
        // AbortError on cancel — silently bail. Anything else, fall back
        // to the input element.
        if ((err as DOMException)?.name === "AbortError") return;
      }
    }
    // Fallback path: trigger the hidden directory input.
    fallbackInputRef.current?.click();
  }, [handleFilesPicked]);

  const onFallbackInputChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const list = e.target.files;
      if (!list) return;
      const files = Array.from(list);
      // Reset value so picking the same folder twice in a row still fires.
      e.target.value = "";
      await handleFilesPicked(files);
    },
    [handleFilesPicked],
  );

  const selectedEntries = useMemo(
    () => entries.filter((e) => e.selected),
    [entries],
  );
  const canStartUpload =
    truckId.length > 0 && selectedEntries.length > 0;

  // Kick off the batch upload when the user advances from `files` -> `upload`.
  const startUpload = useCallback(async () => {
    const rows: UploadRow[] = selectedEntries.map((e) => ({
      id: e.id,
      file: e.file,
      startedAt: fromDatetimeLocal(e.startedAtLocal),
      status: { kind: "pending" },
    }));
    setUploadRows(rows);
    setStep("upload");

    // Worker pool: at most `UPLOAD_CONCURRENCY` uploads in flight at once.
    const queue = [...rows];
    async function worker() {
      while (queue.length > 0) {
        const row = queue.shift();
        if (!row) return;
        await uploadOne(row);
      }
    }

    async function uploadOne(row: UploadRow) {
      setUploadRows((rs) =>
        rs.map((r) =>
          r.id === row.id ? { ...r, status: { kind: "uploading", pct: 0 } } : r,
        ),
      );
      let durationS = 0;
      try {
        durationS = await probeDuration(row.file);
      } catch {
        durationS = 0;
      }
      try {
        const handle = uploadClip({
          file: row.file,
          truck_id: truckId,
          driver_id: driverId || undefined,
          started_at: row.startedAt.toISOString(),
          duration_s: durationS,
          onProgress: (pct) => {
            setUploadRows((rs) =>
              rs.map((r) =>
                r.id === row.id
                  ? { ...r, status: { kind: "uploading", pct } }
                  : r,
              ),
            );
          },
        });
        const detail: ClipDetail = await handle.promise;
        hasSucceededRef.current = true;
        setUploadRows((rs) =>
          rs.map((r) =>
            r.id === row.id
              ? { ...r, status: { kind: "done", clipId: detail.id } }
              : r,
          ),
        );
      } catch (err) {
        const msg = formatUploadError(err);
        setUploadRows((rs) =>
          rs.map((r) =>
            r.id === row.id ? { ...r, status: { kind: "failed", error: msg } } : r,
          ),
        );
      }
    }

    const workers = Array.from({ length: UPLOAD_CONCURRENCY }, () => worker());
    await Promise.all(workers);
    setStep("done");
  }, [selectedEntries, truckId, driverId]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      data-testid="upload-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => {
        // Backdrop click — only while not uploading.
        if (e.target === e.currentTarget && step !== "upload") handleClose();
      }}
    >
      <div className="w-full max-w-3xl space-y-4 rounded-lg border border-border bg-background p-6 shadow-lg">
        <header className="flex items-center justify-between">
          <h2 id={titleId} className="text-lg font-semibold tracking-tight">
            Upload clips
          </h2>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={step === "upload"}
            onClick={handleClose}
            data-testid="upload-modal-close"
          >
            Close
          </Button>
        </header>

        {/* Hidden directory-input fallback. Always mounted so tests can
            drive it directly via fireEvent. */}
        <input
          ref={fallbackInputRef}
          type="file"
          // The `webkitdirectory` attribute is non-standard but supported
          // by all modern browsers; React understands the lowercase form.
          // @ts-expect-error — non-standard attribute
          webkitdirectory=""
          directory=""
          multiple
          accept="video/*"
          className="hidden"
          data-testid="upload-modal-fallback-input"
          onChange={onFallbackInputChange}
        />

        {step === "source" ? (
          <SourceStep onPick={openDirectoryPicker} />
        ) : null}

        {step === "files" ? (
          <FilesStep
            entries={entries}
            setEntries={setEntries}
            overflow={overflow}
            trucks={trucks.data ?? []}
            drivers={drivers.data ?? []}
            truckId={truckId}
            setTruckId={setTruckId}
            driverId={driverId}
            setDriverId={setDriverId}
            canStart={canStartUpload}
            onStart={startUpload}
            onBack={() => setStep("source")}
          />
        ) : null}

        {step === "upload" ? (
          <UploadStep rows={uploadRows} />
        ) : null}

        {step === "done" ? (
          <DoneStep rows={uploadRows} onClose={handleClose} />
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step components — kept inline because they share state with the modal.
// ---------------------------------------------------------------------------

function SourceStep({ onPick }: { onPick: () => void }) {
  return (
    <div className="space-y-3" data-testid="upload-modal-source">
      <p className="text-sm font-medium">
        Pick your SD card or dashcam drive
      </p>
      <p className="text-xs text-muted-foreground">
        We&apos;ll scan for .mp4 / .mov / .mkv / .m4v files (max{" "}
        {MAX_FILES_PER_BATCH} per batch).
      </p>
      <div>
        <Button
          type="button"
          onClick={onPick}
          data-testid="upload-modal-pick-folder"
        >
          Choose folder…
        </Button>
      </div>
    </div>
  );
}

function FilesStep({
  entries,
  setEntries,
  overflow,
  trucks,
  drivers,
  truckId,
  setTruckId,
  driverId,
  setDriverId,
  canStart,
  onStart,
  onBack,
}: {
  entries: FileEntry[];
  setEntries: React.Dispatch<React.SetStateAction<FileEntry[]>>;
  overflow: boolean;
  trucks: TruckOut[];
  drivers: DriverOut[];
  truckId: string;
  setTruckId: (v: string) => void;
  driverId: string;
  setDriverId: (v: string) => void;
  canStart: boolean;
  onStart: () => void;
  onBack: () => void;
}) {
  const noFiles = entries.length === 0;
  return (
    <div className="space-y-3" data-testid="upload-modal-files">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Truck <span className="text-destructive">*</span>
          <select
            value={truckId}
            onChange={(e) => setTruckId(e.target.value)}
            data-testid="upload-modal-truck"
            className={inputCn}
          >
            <option value="">Select a truck…</option>
            {trucks.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Driver
          <select
            value={driverId}
            onChange={(e) => setDriverId(e.target.value)}
            data-testid="upload-modal-driver"
            className={inputCn}
          >
            <option value="">No driver</option>
            {drivers.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {overflow ? (
        <p
          className="rounded-md border border-yellow-500/50 bg-yellow-500/5 p-2 text-xs"
          data-testid="upload-modal-overflow"
        >
          Showing first {MAX_FILES_PER_BATCH} files; narrow your selection or
          upload the rest in another batch.
        </p>
      ) : null}

      {noFiles ? (
        <p
          data-testid="upload-modal-no-videos"
          className="rounded-md border border-dashed border-border p-6 text-center text-xs text-muted-foreground"
        >
          No videos found in the chosen folder.
        </p>
      ) : (
        <div
          className="max-h-72 overflow-y-auto rounded-md border border-border"
          data-testid="upload-modal-file-table"
        >
          <table className="w-full text-sm">
            <thead className="bg-muted text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th className="px-2 py-1.5 text-left">
                  <input
                    type="checkbox"
                    checked={entries.every((e) => e.selected)}
                    onChange={(e) => {
                      const v = e.target.checked;
                      setEntries((rows) =>
                        rows.map((r) => ({ ...r, selected: v })),
                      );
                    }}
                    data-testid="upload-modal-select-all"
                    aria-label="Select all"
                  />
                </th>
                <th className="px-2 py-1.5 text-left">File</th>
                <th className="px-2 py-1.5 text-left">Size</th>
                <th className="px-2 py-1.5 text-left">Date</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id} className="border-t border-border">
                  <td className="px-2 py-1.5">
                    <input
                      type="checkbox"
                      checked={entry.selected}
                      data-testid={`upload-modal-row-${entry.id}-checkbox`}
                      onChange={(e) => {
                        const v = e.target.checked;
                        setEntries((rows) =>
                          rows.map((r) =>
                            r.id === entry.id ? { ...r, selected: v } : r,
                          ),
                        );
                      }}
                    />
                  </td>
                  <td className="max-w-xs truncate px-2 py-1.5 font-mono text-xs">
                    {entry.file.name}
                  </td>
                  <td className="px-2 py-1.5 text-xs text-muted-foreground">
                    {formatBytes(entry.file.size)}
                  </td>
                  <td className="px-2 py-1.5">
                    <input
                      type="datetime-local"
                      value={entry.startedAtLocal}
                      data-testid={`upload-modal-row-${entry.id}-date`}
                      onChange={(e) =>
                        setEntries((rows) =>
                          rows.map((r) =>
                            r.id === entry.id
                              ? { ...r, startedAtLocal: e.target.value }
                              : r,
                          ),
                        )
                      }
                      className="h-8 rounded-md border border-input bg-background px-2 text-xs"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <footer className="flex items-center justify-between">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onBack}
          data-testid="upload-modal-back"
        >
          Back
        </Button>
        <Button
          type="button"
          onClick={onStart}
          disabled={!canStart}
          data-testid="upload-modal-start"
        >
          Start upload
        </Button>
      </footer>
    </div>
  );
}

function UploadStep({ rows }: { rows: UploadRow[] }) {
  return (
    <div className="space-y-3" data-testid="upload-modal-upload">
      <p className="text-sm text-muted-foreground">
        Uploading {rows.length} {rows.length === 1 ? "clip" : "clips"}…
      </p>
      <ul className="divide-y divide-border rounded-md border border-border">
        {rows.map((r) => (
          <li
            key={r.id}
            className="space-y-1 px-3 py-2"
            data-testid={`upload-modal-progress-${r.id}`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-mono text-xs">{r.file.name}</span>
              <StatusBadge status={r.status} />
            </div>
            <ProgressBar status={r.status} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function DoneStep({
  rows,
  onClose,
}: {
  rows: UploadRow[];
  onClose: () => void;
}) {
  const done = rows.filter((r) => r.status.kind === "done").length;
  const failed = rows.filter((r) => r.status.kind === "failed").length;
  return (
    <div className="space-y-3" data-testid="upload-modal-done">
      <p className="text-sm font-medium">
        {done} of {rows.length} uploaded
        {failed > 0 ? `, ${failed} failed` : ""}.
      </p>
      <ul className="divide-y divide-border rounded-md border border-border">
        {rows.map((r) => (
          <li
            key={r.id}
            className="flex items-center justify-between gap-3 px-3 py-2"
            data-testid={`upload-modal-done-${r.id}`}
          >
            <span className="truncate font-mono text-xs">{r.file.name}</span>
            <StatusBadge status={r.status} />
          </li>
        ))}
      </ul>
      <footer className="flex justify-end">
        <Button
          type="button"
          onClick={onClose}
          data-testid="upload-modal-done-close"
        >
          Close
        </Button>
      </footer>
    </div>
  );
}

function StatusBadge({ status }: { status: UploadStatus }) {
  if (status.kind === "pending") {
    return <span className="text-xs text-muted-foreground">Pending</span>;
  }
  if (status.kind === "uploading") {
    return (
      <span className="text-xs text-muted-foreground">
        {Math.round(status.pct)}%
      </span>
    );
  }
  if (status.kind === "done") {
    return <span className="text-xs font-medium text-emerald-600">Done</span>;
  }
  return (
    <span
      className="max-w-[16rem] truncate text-xs font-medium text-destructive"
      title={status.error}
    >
      {status.error}
    </span>
  );
}

function ProgressBar({ status }: { status: UploadStatus }) {
  let pct = 0;
  if (status.kind === "uploading") pct = status.pct;
  if (status.kind === "done") pct = 100;
  const failed = status.kind === "failed";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div
        className={cn(
          "h-full transition-all",
          failed ? "bg-destructive" : "bg-primary",
        )}
        style={{ width: failed ? "100%" : `${pct}%` }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const inputCn = cn(
  "h-9 w-full rounded-md border border-input bg-background px-3 text-sm",
  "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  "disabled:opacity-50",
);

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

/** Convert a `Date` to the value shape required by `<input type="datetime-local">`. */
function toDatetimeLocal(d: Date): string {
  const y = d.getFullYear();
  const m = pad2(d.getMonth() + 1);
  const day = pad2(d.getDate());
  const h = pad2(d.getHours());
  const min = pad2(d.getMinutes());
  return `${y}-${m}-${day}T${h}:${min}`;
}

/** Inverse of `toDatetimeLocal`. Falls back to "now" on garbage input. */
function fromDatetimeLocal(v: string): Date {
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return new Date();
  return d;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatUploadError(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.detail}`;
  if (err instanceof Error) return err.message;
  return "Upload failed";
}

/**
 * Walk a `FileSystemDirectoryHandle` and append video files to
 * `collected`. Bails out early once `maxFiles` are gathered so we don't
 * iterate the entire SD card just to throw the tail away.
 */
async function walkDirectoryHandle(
  dir: unknown,
  collected: File[],
  maxFiles: number,
): Promise<void> {
  // The File System Access types aren't part of TypeScript's lib by
  // default; cast to a minimal interface we can iterate.
  const handle = dir as AsyncIterable<[string, unknown]> & {
    values?: () => AsyncIterable<unknown>;
  };

  const iter = handle.values ? handle.values() : null;
  if (!iter) return;
  for await (const entry of iter) {
    if (collected.length >= maxFiles) return;
    const e = entry as {
      kind: "file" | "directory";
      name: string;
      getFile?: () => Promise<File>;
    };
    if (e.kind === "file" && typeof e.getFile === "function") {
      if (VIDEO_EXT_RE.test(e.name)) {
        const f = await e.getFile();
        collected.push(f);
      }
    } else if (e.kind === "directory") {
      await walkDirectoryHandle(e, collected, maxFiles);
    }
  }
}

/**
 * Read the duration of a video file via a hidden `<video>` element.
 *
 * Resolves with the duration rounded to whole seconds, or `0` if the
 * browser can't read it within {@link DURATION_PROBE_TIMEOUT_MS}.
 */
function probeDuration(file: File): Promise<number> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.preload = "metadata";
    video.muted = true;
    let settled = false;
    const finish = (s: number) => {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(url);
      resolve(s);
    };
    const timeout = window.setTimeout(() => finish(0), DURATION_PROBE_TIMEOUT_MS);
    video.onloadedmetadata = () => {
      window.clearTimeout(timeout);
      const d = Number.isFinite(video.duration) ? Math.max(0, Math.round(video.duration)) : 0;
      finish(d);
    };
    video.onerror = () => {
      window.clearTimeout(timeout);
      finish(0);
    };
    video.src = url;
  });
}
