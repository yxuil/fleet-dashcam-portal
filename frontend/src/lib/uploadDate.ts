/**
 * Helpers for extracting a recording timestamp from a dashcam clip
 * filename.
 *
 * Real dashcam vendors stamp the filename with the start-of-recording
 * timestamp in one of a handful of shapes. We try them in order and
 * return the first match. If nothing matches, the caller falls back to
 * `file.lastModified` — which can drift if a file was copied across
 * filesystems, but it's the best we have without parsing the MP4
 * container.
 *
 * All returned `Date` instances are local-time (no timezone in the
 * filename means we can't know the camera's TZ — the modal's
 * `<input type="datetime-local">` is also in local time, so this is
 * consistent end-to-end).
 *
 * Server-side date extraction via ffprobe is explicitly out of scope —
 * see §10 of `docs/architecture.md`.
 */

/** Regex/parser pairs tried in order. The first to produce a valid `Date` wins. */
const PATTERNS: ReadonlyArray<{
  re: RegExp;
  build: (m: RegExpMatchArray) => Date;
}> = [
  // YYYYMMDD_HHMMSS, e.g. "20260628_143012.mp4".
  {
    re: /(\d{4})(\d{2})(\d{2})[_T](\d{2})(\d{2})(\d{2})/,
    build: (m) =>
      new Date(
        Number(m[1]),
        Number(m[2]) - 1,
        Number(m[3]),
        Number(m[4]),
        Number(m[5]),
        Number(m[6]),
      ),
  },
  // YYYY-MM-DD_HH-MM-SS or YYYY-MM-DD HH-MM-SS, e.g.
  // "2026-06-28_14-30-12.mov" or "2026-06-28 14-30-12.mp4".
  {
    re: /(\d{4})-(\d{2})-(\d{2})[ _](\d{2})-(\d{2})-(\d{2})/,
    build: (m) =>
      new Date(
        Number(m[1]),
        Number(m[2]) - 1,
        Number(m[3]),
        Number(m[4]),
        Number(m[5]),
        Number(m[6]),
      ),
  },
  // YYYY-MM-DD_HH:MM:SS (sometimes used by Linux cameras that preserve
  // colons in filenames).
  {
    re: /(\d{4})-(\d{2})-(\d{2})[ _](\d{2}):(\d{2}):(\d{2})/,
    build: (m) =>
      new Date(
        Number(m[1]),
        Number(m[2]) - 1,
        Number(m[3]),
        Number(m[4]),
        Number(m[5]),
        Number(m[6]),
      ),
  },
];

/**
 * Try to extract a recording timestamp from a dashcam filename.
 *
 * @param name - File name (with or without extension).
 * @returns A local-time `Date` matching the first recognised pattern,
 *   or `null` if no pattern matches.
 */
export function parseDashcamFilenameDate(name: string): Date | null {
  // Strip everything up to and including the last path separator so a
  // caller can pass `webkitRelativePath` (e.g. "DCIM/100MEDIA/...") without
  // accidentally matching a directory name.
  const base = name.replace(/^.*[\\/]/, "");
  for (const { re, build } of PATTERNS) {
    const m = base.match(re);
    if (!m) continue;
    const d = build(m);
    if (!Number.isNaN(d.getTime())) return d;
  }
  return null;
}
