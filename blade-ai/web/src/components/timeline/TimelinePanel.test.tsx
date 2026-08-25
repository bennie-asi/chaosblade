/**
 * TimelinePanel tests — the right rail's process view (design doc
 * §7.2 搬家裁决). Pins: tagged node-progress lines render with the
 * source node, a timestamp relative to the first entry, and the
 * slide-in class; untagged logs and non-log kinds stay out; the empty
 * hint shows before the first line; ts-less items (pre-field
 * hydration) render without a timestamp.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { HistoryItem } from "@blade-ai/core";
import { StoreProvider, configureI18n } from "@blade-ai/core";
import { ui } from "../../lib/uiText";
import { TimelinePanel, formatRelTs } from "./TimelinePanel";

configureI18n("en");

afterEach(() => cleanup());

const T0 = 1_700_000_000_000;

function log(
  id: string,
  tag: string | undefined,
  text: string,
  ts?: number,
): HistoryItem {
  return { kind: "log", id, level: "info", text, tag, ts };
}

function renderPanel(history: HistoryItem[]) {
  return render(
    <StoreProvider initial={{ history }}>
      <TimelinePanel />
    </StoreProvider>,
  );
}

describe("formatRelTs", () => {
  it("formats +m:ss against the first entry, clamping negative deltas", () => {
    expect(formatRelTs(0)).toBe("+0:00");
    expect(formatRelTs(61_000)).toBe("+1:01");
    expect(formatRelTs(-500)).toBe("+0:00");
  });
});

describe("TimelinePanel", () => {
  it("renders tagged progress lines with node name and relative timestamps", () => {
    renderPanel([
      log("n1", "safety_check", "whitelist ok", T0),
      log("n2", "execute", "injecting cpu", T0 + 65_000),
    ]);
    expect(screen.getByText("safety_check")).toBeInTheDocument();
    expect(screen.getByText("whitelist ok")).toBeInTheDocument();
    expect(screen.getByText("injecting cpu")).toBeInTheDocument();
    expect(screen.getByText("+0:00")).toBeInTheDocument();
    expect(screen.getByText("+1:05")).toBeInTheDocument();
  });

  it("scopes to the latest turn — a previous drill's lines stay out", () => {
    renderPanel([
      log("n0", "execute_loop", "drill A line", T0),
      { kind: "user", id: "u1", text: "inject again" },
      log("n1", "execute_loop", "drill B line", T0 + 5_000),
    ]);
    expect(screen.queryByText("drill A line")).toBeNull();
    expect(screen.getByText("drill B line")).toBeInTheDocument();
  });

  it("keeps untagged logs and non-log kinds out of the timeline", () => {
    renderPanel([
      log("s1", undefined, "slash command output"),
      { kind: "agent", id: "a1", text: "agent reply" },
      log("n1", "baseline", "3/5 captured"),
    ]);
    expect(screen.queryByText("slash command output")).toBeNull();
    expect(screen.queryByText("agent reply")).toBeNull();
    expect(screen.getByText("3/5 captured")).toBeInTheDocument();
  });

  it("shows the empty hint before the first progress line", () => {
    renderPanel([]);
    expect(screen.getByText(ui().timelineEmptyHint)).toBeInTheDocument();
  });

  it("renders ts-less items (pre-field hydration) without a timestamp", () => {
    renderPanel([log("n1", "execute", "legacy line")]);
    expect(screen.getByText("legacy line")).toBeInTheDocument();
    expect(screen.queryByText(/^\+/)).toBeNull();
  });

  it("marks each entry with the slide-in animation class", () => {
    const { container } = renderPanel([log("n1", "execute", "x", T0)]);
    expect(container.querySelectorAll(".timeline-in")).toHaveLength(1);
  });
});
