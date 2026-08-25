/**
 * SpanWaterfall: proportional bar geometry, error/interrupt glyphs,
 * in-place error expansion, LLM-token accent colouring, and the
 * summary line's arithmetic consistency with the rows.
 *
 * Pure component tests (no router, no client) — the waterfall takes
 * the spans array directly.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { configureI18n } from "@blade-ai/core";
import { SpanWaterfall } from "./SpanWaterfall";
import { ui } from "../../lib/uiText";

configureI18n("en");

afterEach(cleanup);

const BASE = 1_000.0; // t0, seconds

function span(over: Record<string, unknown>): Record<string, unknown> {
  return {
    node_name: "node",
    start_time: BASE,
    duration_ms: 1000,
    ...over,
  };
}

describe("SpanWaterfall", () => {
  it("renders the explicit empty state for no spans", () => {
    render(<SpanWaterfall spans={[]} />);
    expect(screen.getByText(ui().taskDetailSpansNone)).toBeInTheDocument();
  });

  it("positions bars proportionally to the window", () => {
    // Window = max(offset+duration) = 10s. Row "a": 0→50%, row "b":
    // starts at 5s (left 50%) and runs 5s (width 50%).
    const { container } = render(
      <SpanWaterfall
        spans={[
          span({ node_name: "a", duration_ms: 5000 }),
          span({ node_name: "b", start_time: BASE + 5, duration_ms: 5000 }),
        ]}
      />,
    );
    const bars = container.querySelectorAll<HTMLElement>(".absolute.h-full");
    expect(bars).toHaveLength(2);
    expect(bars[0]?.style.left).toBe("0%");
    expect(bars[0]?.style.width).toBe("50%");
    expect(bars[1]?.style.left).toBe("50%");
    expect(bars[1]?.style.width).toBe("50%");
  });

  it("marks an interrupted span with ⏸ and no expandable error", () => {
    render(
      <SpanWaterfall
        spans={[span({ node_name: "gate", error: "interrupted by user" })]}
      />,
    );
    expect(screen.getByText("⏸")).toBeInTheDocument();
    // Interruption is not a failure: no ✗, and no click-to-expand
    // affordance (role=button is only set on real errors).
    expect(screen.queryByText("✗")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("marks a failed span with ✗ and expands the full error on click", () => {
    render(
      <SpanWaterfall
        spans={[span({ node_name: "exec", error: "blade create: target NotFound" })]}
      />,
    );
    expect(screen.getByText("✗")).toBeInTheDocument();
    // Collapsed: the error text is not in the document yet.
    expect(
      screen.queryByText("blade create: target NotFound"),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button"));
    expect(
      screen.getByText("blade create: target NotFound"),
    ).toBeInTheDocument();
    // Toggles back.
    fireEvent.click(screen.getByRole("button"));
    expect(
      screen.queryByText("blade create: target NotFound"),
    ).toBeNull();
  });

  it("colours the bar by semantics: error red / interrupt grey / LLM accent / clean green", () => {
    const { container } = render(
      <SpanWaterfall
        spans={[
          span({ node_name: "failed-node", error: "boom" }),
          span({ node_name: "cut", error: "interrupted" }),
          span({ node_name: "think", token_input: 10, token_output: 5 }),
          span({ node_name: "pure-tool", tool_calls: ["kubectl_get"] }),
        ]}
      />,
    );
    const bars = [...container.querySelectorAll<HTMLElement>(".absolute.h-full")];
    const cls = (i: number) => bars[i]?.className ?? "";
    expect(cls(0)).toContain("bg-danger");
    expect(cls(1)).toContain("bg-forge-text-faint");
    expect(cls(2)).toContain("bg-forge-accent");
    expect(cls(3)).toContain("bg-success-dot");
  });

  it("keeps the summary line consistent with the rows", () => {
    // 3 nodes, window 10s; tokens only on "think" (120↓30↑); tools 3
    // across two rows; confirmation_gate takes 5s of the 10s → 50%.
    render(
      <SpanWaterfall
        spans={[
          span({ node_name: "plan", duration_ms: 1000 }),
          span({
            node_name: "confirmation_gate",
            start_time: BASE + 1,
            duration_ms: 5000,
          }),
          span({
            node_name: "think",
            start_time: BASE + 6,
            duration_ms: 4000,
            token_input: 120,
            token_output: 30,
            tool_calls: ["a", "a", "b"],
          }),
        ]}
      />,
    );
    expect(
      screen.getByText(
        /3 nodes · Σ 10s · tokens 120↓30↑ · tools ×3 · confirm gate 5s \(50%\)/,
      ),
    ).toBeInTheDocument();
    // Row meta aggregates the repeated tool name.
    expect(screen.getByText(/4s · 120↓30↑ · a×2 b×1/)).toBeInTheDocument();
  });

  it("omits token / tool / confirm segments when absent", () => {
    render(<SpanWaterfall spans={[span({ node_name: "bare" })]} />);
    const summary = screen.getByText(/1 nodes · Σ 1s/);
    expect(summary.textContent).not.toContain("tokens");
    expect(summary.textContent).not.toContain("tools");
    expect(summary.textContent).not.toContain("confirm");
  });
});
