import { describe, expect, it } from "vitest";

import { parseDashcamFilenameDate } from "./uploadDate";

describe("parseDashcamFilenameDate", () => {
  it("parses YYYYMMDD_HHMMSS filenames", () => {
    const d = parseDashcamFilenameDate("20260628_143012.mp4");
    expect(d).not.toBeNull();
    expect(d!.getFullYear()).toBe(2026);
    expect(d!.getMonth()).toBe(5); // June, zero-indexed
    expect(d!.getDate()).toBe(28);
    expect(d!.getHours()).toBe(14);
    expect(d!.getMinutes()).toBe(30);
    expect(d!.getSeconds()).toBe(12);
  });

  it("parses YYYY-MM-DD_HH-MM-SS filenames", () => {
    const d = parseDashcamFilenameDate("2026-06-28_14-30-12.mov");
    expect(d).not.toBeNull();
    expect(d!.getFullYear()).toBe(2026);
    expect(d!.getMonth()).toBe(5);
    expect(d!.getDate()).toBe(28);
    expect(d!.getHours()).toBe(14);
  });

  it("parses YYYY-MM-DD HH-MM-SS filenames (space separator)", () => {
    const d = parseDashcamFilenameDate("2026-06-28 14-30-12.mkv");
    expect(d).not.toBeNull();
    expect(d!.getHours()).toBe(14);
    expect(d!.getMinutes()).toBe(30);
  });

  it("parses YYYYMMDDTHHMMSS filenames (ISO-ish)", () => {
    const d = parseDashcamFilenameDate("DCIM/20260628T143012.m4v");
    expect(d).not.toBeNull();
    expect(d!.getFullYear()).toBe(2026);
    expect(d!.getMonth()).toBe(5);
    expect(d!.getHours()).toBe(14);
  });

  it("returns null when no pattern matches", () => {
    expect(parseDashcamFilenameDate("random_clip.mp4")).toBeNull();
    expect(parseDashcamFilenameDate("garbage.mov")).toBeNull();
  });

  it("ignores directory portion of webkitRelativePath", () => {
    // The directory contains digits — but the filename itself is not a
    // recognisable timestamp, so the result must be null rather than
    // matching against the path.
    expect(
      parseDashcamFilenameDate("camera-20260101/clip-A.mp4"),
    ).toBeNull();
  });
});
