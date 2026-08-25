/**
 * LiveThinkingPanel tests — the web's live chain-of-thought fold.
 *
 * Pinned behaviours:
 *   - invisible unless a thinking session is streaming
 *   - collapsed by default: shimmer header + ticking elapsed, no body
 *   - click toggles the raw CoT body (aria-expanded mirrors it)
 *   - the elapsed ticker advances on a 1s interval
 *
 * The committed-chip side (duration-only ThinkingItem) is covered in
 * messages.test.tsx; buffer lifecycle / suppression semantics are
 * covered in core reducer tests.
 */
import { act } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StoreProvider, configureI18n, t } from "@blade-ai/core";
import { LiveThinkingPanel } from "./ThinkingPanel";

configureI18n("en");

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function renderPanel(opts?: {
  buffer?: string;
  startedAgoMs?: number;
  active?: boolean;
}) {
  const active = opts?.active ?? true;
  return render(
    <StoreProvider
      initial={{
        hasActiveThinking: active,
        thoughtBuffer: opts?.buffer ?? "checking the pod selectors…",
        thoughtStartedAt: Date.now() - (opts?.startedAgoMs ?? 0),
      }}
    >
      <LiveThinkingPanel />
    </StoreProvider>,
  );
}

describe("LiveThinkingPanel", () => {
  it("renders nothing when no thinking session is active", () => {
    const { container } = renderPanel({ active: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the shimmer header collapsed by default (no body)", () => {
    renderPanel();
    const header = screen.getByRole("button", {
      name: new RegExp(t("thinking.live")),
    });
    expect(header.getAttribute("aria-expanded")).toBe("false");
    // The shimmer class is the sanctioned "live" signal (design §5.3).
    expect(header.querySelector(".thinking-shimmer")).not.toBeNull();
    expect(header.textContent).toContain("▸");
    // Collapsed → the buffer text is NOT on screen.
    expect(screen.queryByText(/checking the pod selectors/)).toBeNull();
  });

  it("click toggles the CoT body open and closed", () => {
    renderPanel({ buffer: "step 1: list pods" });
    const header = screen.getByRole("button", {
      name: new RegExp(t("thinking.live")),
    });

    fireEvent.click(header);
    expect(header.getAttribute("aria-expanded")).toBe("true");
    expect(header.textContent).toContain("▾");
    expect(screen.getByText("step 1: list pods")).toBeTruthy();

    fireEvent.click(header);
    expect(header.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("step 1: list pods")).toBeNull();
  });

  it("ticks the elapsed counter once per second", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-18T12:00:00Z"));
    renderPanel({ startedAgoMs: 5000 });

    const header = screen.getByRole("button", {
      name: new RegExp(t("thinking.live")),
    });
    expect(header.textContent).toContain("· 5s");

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(header.textContent).toContain("· 6s");
  });
});
