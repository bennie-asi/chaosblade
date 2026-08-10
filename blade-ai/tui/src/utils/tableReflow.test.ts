/**
 * Tests for ``reflowWideTables`` — the pre-marked guard that
 * downgrades over-wide GFM tables to a vertical record list so
 * Ink's ``<Text wrap="wrap">`` never hard-breaks a box-drawn table
 * mid-cell.
 *
 * Behaviour families:
 *   1. Fitting tables pass through byte-identical.
 *   2. Over-wide tables become per-row record bullets with header
 *      cells as field labels.
 *   3. Fenced code blocks are never touched.
 *   4. CJK double-width headers count toward the width budget.
 *   5. Streaming-partial tables degrade gracefully.
 */

import { describe, expect, it } from "vitest";
import { reflowWideTables } from "./tableReflow.js";

const LONG_PVC =
  "mse-119ca9b37-1739260580650-reg-center-0-datadir-mse-119ca9b37-1739260580650-reg-center-0-0 (30Gi, RWO)";

const WIDE_TABLE = [
  "| Pod 名称 | 状态 | PVC |",
  "|---------|------|-----|",
  "| `mse-0-0` | Running (33h) | " + LONG_PVC + " |",
  "| `mse-0-1` | Running (36d) | 需进一步确认 |",
].join("\n");

const NARROW_TABLE = [
  "| 名称 | 状态 |",
  "|------|------|",
  "| a | Running |",
  "| b | Pending |",
].join("\n");

describe("reflowWideTables / passthrough", () => {
  it("leaves a fitting table byte-identical", () => {
    expect(reflowWideTables(NARROW_TABLE, 80)).toBe(NARROW_TABLE);
  });

  it("leaves prose with stray pipes untouched", () => {
    const text = "Use `a | b` pipelines.\n\nNo table here | really not.";
    expect(reflowWideTables(text, 40)).toBe(text);
  });

  it("leaves text without any pipes untouched", () => {
    expect(reflowWideTables("plain text\n\nmore text", 40)).toBe(
      "plain text\n\nmore text",
    );
  });
});

describe("reflowWideTables / conversion", () => {
  it("converts an over-wide table to record paragraphs", () => {
    const out = reflowWideTables(WIDE_TABLE, 80);
    // One ideographic-space-indented paragraph per data row, labelled
    // by header cells.
    expect(out).toContain("\u3000**Pod 名称**: `mse-0-0`");
    expect(out).toContain("**状态**: Running (33h)");
    expect(out).toContain("**PVC**: " + LONG_PVC);
    expect(out).toContain("\u3000**Pod 名称**: `mse-0-1`");
    // No leftover table chrome.
    expect(out).not.toContain("|");
    expect(out).not.toContain("-----");
  });

  it("preserves surrounding prose around a converted table", () => {
    const text = "候选如下：\n\n" + WIDE_TABLE + "\n\n**推荐目标**：`mse-0-0`";
    const out = reflowWideTables(text, 80);
    expect(out.startsWith("候选如下：\n\n")).toBe(true);
    expect(out.endsWith("**推荐目标**：`mse-0-0`")).toBe(true);
  });

  it("labels unnamed columns positionally", () => {
    const table = [
      "|  | 值 |",
      "|--|----|",
      "| " + "x".repeat(90) + " | ok |",
    ].join("\n");
    const out = reflowWideTables(table, 40);
    expect(out).toContain("\u3000**col 1**:");
    expect(out).toContain("**值**: ok");
  });
});

describe("reflowWideTables / code fences", () => {
  it("never touches table-shaped content inside ``` fences", () => {
    const text = "```\n" + WIDE_TABLE + "\n```";
    expect(reflowWideTables(text, 40)).toBe(text);
  });

  it("still converts a real table after a closed fence", () => {
    const text = "```\ncode\n```\n\n" + WIDE_TABLE;
    const out = reflowWideTables(text, 80);
    expect(out).toContain("```\ncode\n```");
    expect(out).toContain("\u3000**Pod 名称**: `mse-0-0`");
  });
});

describe("reflowWideTables / CJK width accounting", () => {
  it("counts CJK headers as double-width", () => {
    // 20 CJK chars = 40 visual cells; JS length is only 20, so a
    // naive char-count would call this table narrow.
    const table = [
      "| " + "名".repeat(20) + " |",
      "|----------------------|",
      "| v |",
    ].join("\n");
    const out = reflowWideTables(table, 30);
    expect(out).not.toContain("|");
  });
});

describe("reflowWideTables / streaming tolerance", () => {
  it("ignores a table whose separator hasn't arrived yet", () => {
    const partial = "| Pod 名称 | 状态 |";
    expect(reflowWideTables(partial, 40)).toBe(partial);
  });

  it("renders completed rows of a still-streaming table", () => {
    const partial =
      WIDE_TABLE.split("\n").slice(0, 3).join("\n") +
      "\n| `mse-0-2` | Running (5d"; // mid-cell, no trailing pipe
    const out = reflowWideTables(partial, 80);
    expect(out).toContain("\u3000**Pod 名称**: `mse-0-0`");
    // The unfinished row stays raw text until it completes.
    expect(out).toContain("| `mse-0-2` | Running (5d");
  });

  it("handles escaped pipes inside cells", () => {
    const table = [
      "| cmd | 说明 |",
      "|-----|------|",
      "| `a \\| b` | " + "y".repeat(80) + " |",
    ].join("\n");
    const out = reflowWideTables(table, 40);
    expect(out).toContain("**cmd**: `a | b`");
  });
});
