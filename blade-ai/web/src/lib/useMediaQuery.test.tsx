/**
 * useMediaQuery tests — jsdom ships no matchMedia, so both the
 * no-matchMedia fallback and the stubbed-query path get pinned, plus
 * subscribe-identity stability: a fresh closure per render would make
 * useSyncExternalStore tear down and re-add the listener on every
 * consumer render (HistoryList re-renders per streamed token).
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useMediaQuery } from "./useMediaQuery";

const QUERY = "(min-width: 1250px)";

function Probe() {
  const matches = useMediaQuery(QUERY);
  return <span data-testid="probe">{String(matches)}</span>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** Stub matchMedia; returns a log of addEventListener call counts. */
function stubMatchMedia(matches: boolean) {
  let subscriptions = 0;
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches,
    media: query,
    addEventListener: () => {
      subscriptions += 1;
    },
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }));
  return () => subscriptions;
}

describe("useMediaQuery", () => {
  it("reports false where matchMedia is missing (jsdom default)", () => {
    const { getByTestId } = render(<Probe />);
    expect(getByTestId("probe").textContent).toBe("false");
  });

  it("reflects the stubbed query result", () => {
    stubMatchMedia(true);
    const { getByTestId } = render(<Probe />);
    expect(getByTestId("probe").textContent).toBe("true");
  });

  it("keeps a single subscription across consumer re-renders", () => {
    const subscriptionCount = stubMatchMedia(true);
    const { rerender } = render(<Probe />);
    rerender(<Probe />);
    rerender(<Probe />);
    expect(subscriptionCount()).toBe(1);
  });
});
