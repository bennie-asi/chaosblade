/**
 * Pure formatter contracts — the compact duration style
 * (formatDuration) shared by chat chips and task rows, and the
 * turn-end metadata row's token count (formatTokens) + commit stamp
 * (formatLocalTime).
 */
import { describe, expect, it } from "vitest";
import {
  formatDateTime,
  formatDuration,
  formatLocalTime,
  formatTimeOfDay,
  formatTokens,
} from "./format";

describe("formatDuration", () => {
  it("collapses sub-second durations", () => {
    expect(formatDuration(999)).toBe("<1s");
  });

  it("renders seconds below a minute", () => {
    expect(formatDuration(1000)).toBe("1s");
    expect(formatDuration(59_000)).toBe("59s");
  });

  it("renders minutes with zero-padded seconds", () => {
    expect(formatDuration(61_000)).toBe("1m01s");
    expect(formatDuration(600_000)).toBe("10m00s");
  });
});

describe("formatTokens", () => {
  it("keeps sub-1000 counts exact", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(287)).toBe("287");
    expect(formatTokens(999)).toBe("999");
  });

  it("collapses ≥1000 to X.Yk", () => {
    expect(formatTokens(1000)).toBe("1.0k");
    expect(formatTokens(12000)).toBe("12.0k");
    expect(formatTokens(4567)).toBe("4.6k");
  });
});

describe("formatLocalTime", () => {
  it("renders MM-DD HH:MM:SS with zero padding", () => {
    // Local-component constructor → the assertion is timezone-proof.
    const t = new Date(2026, 7, 5, 14, 32, 7).getTime();
    expect(formatLocalTime(t)).toBe("08-05 14:32:07");
  });

  it("always pads single-digit fields to two digits", () => {
    const t = new Date(2026, 0, 2, 3, 4, 5).getTime();
    expect(formatLocalTime(t)).toBe("01-02 03:04:05");
  });
});

describe("formatTimeOfDay", () => {
  it("renders the local HH:MM:SS of an ISO timestamp", () => {
    const iso = new Date(2026, 7, 20, 14, 30, 5).toISOString();
    expect(formatTimeOfDay(iso)).toBe("14:30:05");
  });

  it("passes through empty and unparseable input", () => {
    expect(formatTimeOfDay("")).toBe("");
    expect(formatTimeOfDay("not-a-date")).toBe("not-a-date");
  });
});

describe("formatDateTime", () => {
  it("renders YYYY-MM-DD HH:MM:SS with zero padding", () => {
    const iso = new Date(2026, 7, 20, 14, 30, 5).toISOString();
    expect(formatDateTime(iso)).toBe("2026-08-20 14:30:05");
  });

  it("passes through empty and unparseable input", () => {
    expect(formatDateTime("")).toBe("");
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });
});
