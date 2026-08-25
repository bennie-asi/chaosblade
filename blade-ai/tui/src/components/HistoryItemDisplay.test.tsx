/**
 * HistoryItemDisplay regression pin — the TUI must NOT render
 * ``phase_stepper`` history items.
 *
 * Background: 65ee010 removed the original TUI stepper; P2 (35d0b7c)
 * re-introduced it via shared core for the web UI and accidentally
 * revived the TUI rendering. The removal is now render-layer only
 * (core still appends the item to history for web replay), so this
 * test is the tripwire against a second resurrection: if anyone
 * re-points the case at a real component, it fails.
 */
import { render as inkRender } from "ink-testing-library";
import { describe, expect, it } from "vitest";
import type { LogItem, PhaseStepperItem } from "@blade-ai/core";
import { HistoryItemDisplay } from "./HistoryItemDisplay.js";

describe("HistoryItemDisplay — phase_stepper suppression", () => {
  it("renders a phase_stepper item as empty output (no stray blank rows)", () => {
    const item: PhaseStepperItem = {
      kind: "phase_stepper",
      id: "ps-1",
      steps: [
        { id: "intent", status: "completed" },
        { id: "safety", status: "failed" },
      ],
    };
    const { lastFrame } = inkRender(<HistoryItemDisplay item={item} />);
    // null render → no frame content at all. Anything else (glyphs,
    // a blank row from a wrapper Box) means the suppression regressed.
    expect(lastFrame() ?? "").toBe("");
  });

  it("still dispatches ordinary items (log) to their component", () => {
    const item: LogItem = {
      kind: "log",
      id: "log-1",
      level: "info",
      text: "hello pipeline",
    };
    const { lastFrame } = inkRender(<HistoryItemDisplay item={item} />);
    expect(lastFrame()).toContain("hello pipeline");
  });
});
