/**
 * Tests for ``renderMarkdown`` — focused on the post-hoc shred guard.
 *
 * ``reflowWideTables`` decides table conversion from ``naturalWidth``,
 * a heuristic estimate of marked-terminal's actual box width. When
 * the estimate undershoots (observed 283 estimated vs 289 actual on
 * the drill summary table below), a terminal width inside the gap
 * window passes the table through unconverted and the overflow
 * shreds the box frame in Ink. The guard re-measures the RENDERED
 * output and forces record form on any over-wide border line.
 *
 * Behaviour families:
 *   1. Gap-window widths fall back to record form — no box-drawing
 *      chars survive, every rendered line fits the budget.
 *   2. Widths above the actual box width still render a real box.
 *   3. Prose and narrow tables are untouched by the guard.
 */

import { describe, expect, it } from "vitest";
import { renderMarkdown } from "./markdown.js";
import { visualLen } from "./tableReflow.js";

/** Real incident fixture: the drill execution-result table whose
 *  naturalWidth estimate (283) undershoots marked-terminal's actual
 *  rendered box width (289) by 6 chars. */
const INCIDENT_TABLE = [
  "| Step | Action | Receipt |",
  "|------|--------|---------|",
  "| 1. Inject | `kubectl exec chaosblade-tool-krp7v -n chaosblade -- blade create mem load --mode ram --mem-percent 80 --timeout 300` (fallback exec carrier — operator CRD path unusable due to ImagePullBackOff) | `{\"code\":200,\"success\":true}` → **UID `bb195c28adcda177`** |",
  '| 2. Status check | `blade status bb195c28adcda177` inside the tool pod | `Status: Success`, `Error: ""`, flags confirmed: `--mode=ram --mem-percent=80` |',
].join("\n");

const BOX_CHARS = /[─│┌┐└┘├┤┬┴┼]/;

function stripAnsi(s: string): string {
  return s.replace(/\x1b\[[0-9;]*m/g, "");
}

describe("renderMarkdown / post-hoc shred guard", () => {
  it("gap-window width downgrades to record form instead of shredding", () => {
    // naturalWidth estimates 283, so widths 284..288 skip the
    // pre-parse conversion; the actual 289-wide box would shred.
    // The guard must catch the overflow and force record form.
    for (const width of [284, 286, 288]) {
      const out = stripAnsi(renderMarkdown(INCIDENT_TABLE, width));
      expect(out, `width=${width} must not contain box chars`).not.toMatch(
        BOX_CHARS,
      );
      // Record form keeps every field labelled — no data loss.
      expect(out).toContain("Step");
      expect(out).toContain("Receipt");
      expect(out).toContain("bb195c28adcda177");
    }
  });

  it("every rendered line fits the width budget in the gap window", () => {
    const width = 285;
    const out = stripAnsi(renderMarkdown(INCIDENT_TABLE, width));
    for (const line of out.split("\n")) {
      expect(
        visualLen(line),
        `line over budget at width=${width}: ${line.slice(0, 40)}…`,
      ).toBeLessThanOrEqual(width);
    }
  });

  it("width above the actual box width still renders a real table", () => {
    const out = stripAnsi(renderMarkdown(INCIDENT_TABLE, 320));
    expect(out).toMatch(BOX_CHARS);
    expect(out).toContain("Step");
  });

  it("does not fire on over-long fenced code lines without tables", () => {
    // A long code line overflows the budget but carries no box
    // chars — the guard must not touch the document (no tables to
    // convert anyway; output stays the code block).
    const longLine = "x".repeat(200);
    const text = "```\n" + longLine + "\n```";
    const out = stripAnsi(renderMarkdown(text, 80));
    expect(out).toContain(longLine);
  });

  it("leaves narrow tables and plain prose untouched", () => {
    const narrow = ["| a | b |", "|---|---|", "| 1 | 2 |"].join("\n");
    const out = stripAnsi(renderMarkdown(narrow, 80));
    expect(out).toMatch(BOX_CHARS);
    expect(stripAnsi(renderMarkdown("hello world", 80))).toBe("hello world");
  });
});
