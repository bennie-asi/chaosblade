/**
 * HistoryList node-progress gating (design doc §7.2 搬家裁决):
 * tagged node-progress lines leave the chat column only while the
 * right rail owns them — ProgressRailContext = true (drill live +
 * expanded + viewport ≥1250px, the single rule ChatPage computes);
 * in every other layout they stay — the LivePhaseStepper fallback
 * rule generalised, so the signal never disappears outright.
 *
 * The context defaults to false (no provider), which is why
 * pre-existing HistoryList consumers keep tagged lines unpinned in
 * tests that never mount the provider.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { HistoryItem } from "@blade-ai/core";
import { StoreProvider, configureI18n } from "@blade-ai/core";
import { ProgressRailContext } from "../rail/ProgressRailContext";
import { ui } from "../../lib/uiText";
import { HistoryList } from "./HistoryList";

configureI18n("en");

afterEach(cleanup);

const PROGRESS: HistoryItem = {
  kind: "log",
  id: "n1",
  level: "info",
  text: "baseline 3/5",
  tag: "baseline",
};
const PLAIN_LOG: HistoryItem = {
  kind: "log",
  id: "s1",
  level: "info",
  text: "slash output",
};

function renderList(railVisible?: boolean) {
  const list = (
    <StoreProvider initial={{ history: [PROGRESS, PLAIN_LOG] }}>
      <HistoryList />
    </StoreProvider>
  );
  render(
    railVisible === undefined ? (
      list
    ) : (
      <ProgressRailContext.Provider value={railVisible}>
        {list}
      </ProgressRailContext.Provider>
    ),
  );
}

describe("HistoryList node-progress gating", () => {
  it("drops tagged progress lines while the rail is on screen", () => {
    renderList(true);
    expect(screen.queryByText("baseline 3/5")).toBeNull();
    expect(screen.getByText("slash output")).toBeInTheDocument();
  });

  it("keeps them when the rail is away (collapsed / narrow / no drill)", () => {
    renderList(false);
    expect(screen.getByText("baseline 3/5")).toBeInTheDocument();
  });

  it("keeps them with no provider at all (context defaults to rail-away)", () => {
    renderList();
    expect(screen.getByText("baseline 3/5")).toBeInTheDocument();
  });
});

describe("HistoryList empty-conversation hero (mock v2 scene 1)", () => {
  it("renders greeting + starter chips while history and pending are empty", () => {
    render(
      <StoreProvider>
        <HistoryList onSuggest={() => undefined} />
      </StoreProvider>,
    );
    // Time-of-day greeting text varies by wall clock — assert on the
    // stable sub line + all four chip labels instead.
    expect(screen.getByText(ui().heroSub)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: ui().heroChipCpu }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: ui().heroChipNetwork }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: ui().heroChipRecent }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: ui().heroChipWhat }),
    ).toBeInTheDocument();
  });

  it("chip click hands its label to onSuggest (fill, never send)", () => {
    const onSuggest = vi.fn();
    render(
      <StoreProvider>
        <HistoryList onSuggest={onSuggest} />
      </StoreProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: ui().heroChipCpu }));
    expect(onSuggest).toHaveBeenCalledWith(ui().heroChipCpu);
    expect(onSuggest).toHaveBeenCalledTimes(1);
  });

  it("hides the hero once any item lands (pending counts as conversation)", () => {
    render(
      <StoreProvider
        initial={{
          history: [PLAIN_LOG],
        }}
      >
        <HistoryList onSuggest={() => undefined} />
      </StoreProvider>,
    );
    expect(screen.queryByText(ui().heroSub)).toBeNull();
    expect(screen.getByText("slash output")).toBeInTheDocument();
  });
});
