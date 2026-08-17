/**
 * LogMessage visual-contract tests — node progress readout rail.
 *
 * The readout (``◎ …``) shares the conversation's left rail: the same
 * 2-column indent as the thinking (▸) and agent (⏺) leaders, and the
 * same one-row top spacer every other message kind carries — progress
 * lines never glue to the content above or to each other.
 *
 * Why pin these: the readout used to render at indent 4 with no top
 * margin — glued to the line above and off-rail. A refactor that
 * restores the old padding or drops the spacer would fail here with
 * a precise diff instead of a user screenshot.
 */

import { render as inkRender } from "ink-testing-library";
import { describe, expect, it } from "vitest";
import { LogMessage } from "./LogMessage.js";
import type { LogItem } from "../../state/types.js";
import { Icons } from "../../theme/icons.js";

function readout(overrides: Partial<LogItem> = {}): LogItem {
  return {
    kind: "log",
    id: "nm-test-1",
    level: "info",
    text: "Pre-task probes done (1 ok, 1 warning).",
    tag: "safety_check",
    ...overrides,
  };
}

/** Visual rows of the rendered frame (after stripping the trailing
 *  newline ink-testing-library tacks on). Internal blanks kept. */
function frameRows(frame: string | undefined): string[] {
  if (!frame) return [];
  const lines = frame.split("\n");
  while (lines.length > 0 && lines[lines.length - 1]?.trim() === "") {
    lines.pop();
  }
  return lines;
}

describe("LogMessage / node progress readout rail", () => {
  it("reticle sits on the shared left rail (indent 2, same as ▸ / ⏺)", () => {
    const { lastFrame } = inkRender(<LogMessage item={readout()} />);
    const rows = frameRows(lastFrame());
    const body = rows.find((r) => r.includes(Icons.scope));
    expect(body).toBeDefined();
    // Exactly two leading spaces — the rail ThinkingMessage and
    // AgentMessage use (paddingLeft={2}), not the old indent-4 hole.
    expect(body!.startsWith(`  ${Icons.scope}`)).toBe(true);
    expect(body!.startsWith(`    ${Icons.scope}`)).toBe(false);
  });

  it("every readout carries the one-row spacer above", () => {
    // Same contract as thinking / agent / tool items: marginTop 1.
    // Consecutive readouts must NOT stack tight — the glue-to-the-
    // line-above defect this suite exists to prevent.
    const { lastFrame } = inkRender(<LogMessage item={readout()} />);
    const rows = frameRows(lastFrame());
    expect(rows[0]?.trim()).toBe("");
    expect(rows[1]?.includes(Icons.scope)).toBe(true);
  });

  it("leaderless slash output keeps its own spacer (untouched path)", () => {
    const { lastFrame } = inkRender(
      <LogMessage item={{ ...readout(), tag: undefined }} />,
    );
    const rows = frameRows(lastFrame());
    // Slash branch renders marginTop={1} and no reticle glyph.
    expect(rows[0]?.trim()).toBe("");
    expect(rows[1]?.includes(Icons.scope)).toBe(false);
  });
});
