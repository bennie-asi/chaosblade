/**
 * ChatPage width-breakpoint tests (task 2.6 / 2.8 — mock v2 layout rule):
 * one boolean gates three consumers — the right progress rail renders
 * only while drill-live AND expanded AND viewport ≥1250px
 * (``RAIL_MIN_WIDTH_PX``), and the same boolean switches the chat
 * column's measure 980px ↔ 780px and HistoryList's node-progress
 * fallback. jsdom ships no matchMedia, so the viewport is stubbed per
 * test (same helper shape as useMediaQuery.test).
 *
 * The drill is driven live the honest way: ``lastTaskId`` seeded in the
 * store + a non-terminal metric from the stubbed client. The seeded
 * tagged node-progress line is the non-vacuity witness — it leaves the
 * chat column only once the metric has resolved AND the rail is on
 * screen, so its absence/presence proves the rule actually fired.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  StoreProvider,
  configureI18n,
  type BladeClient,
  type HistoryItem,
} from "@blade-ai/core";
import { BootContext, type BootContextValue } from "./bootContext";
import { ui } from "../lib/uiText";
import { useRailCollapsed } from "../components/rail/railStore";
import { useSessionPanelCollapsed } from "../components/chat/SessionPanel";
import { ChatPage } from "./ChatPage";

configureI18n("en");

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  useRailCollapsed.resetForTests();
  useSessionPanelCollapsed.resetForTests();
});

/** Non-terminal drill metric — the rail's live signal (task_state
 *  ``injected`` is mid-lifecycle, see useActiveDrill). */
const LIVE_METRIC: Record<string, unknown> = {
  task_id: "t1",
  task_state: "injected",
  status: "in_progress",
  phase: "executing",
};

const PROGRESS: HistoryItem = {
  kind: "log",
  id: "n1",
  level: "info",
  text: "baseline 3/5",
  tag: "baseline",
};

/** Stub the viewport width class (jsdom has no matchMedia). */
function stubViewport(wide: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }));
}

function renderChat(wide: boolean): { queryClient: QueryClient } {
  stubViewport(wide);
  const client = {
    listSessions: vi.fn().mockResolvedValue([]),
    getTaskMetric: vi.fn().mockResolvedValue(LIVE_METRIC),
  } as unknown as BladeClient;
  const boot: BootContextValue = {
    client,
    activeSessionId: "s1",
    resetSession: vi.fn().mockResolvedValue(undefined),
    switchSession: vi.fn().mockResolvedValue(undefined),
    langChoice: "browser",
    setLangChoice: () => {},
  };
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <StoreProvider initial={{ lastTaskId: "t1", history: [PROGRESS] }}>
        <BootContext.Provider value={boot}>
          <ChatPage />
        </BootContext.Provider>
      </StoreProvider>
    </QueryClientProvider>,
  );
  return { queryClient, ...utils };
}

/** Every element carrying the chat column's measure class — History-
 *  List's column AND the Composer's two wrappers all switch on the one
 *  railVisible boolean, so the full set must agree. The measure class
 *  IS the pinned behaviour, hence the class query; the scan is manual
 *  because jsdom's selector engine chokes on the bracket inside an
 *  attribute selector. */
function chatMeasures(): string[] {
  const found: string[] = [];
  for (const el of document.querySelectorAll("div")) {
    const m = el.className.match(/max-w-\[\d+px\]/);
    if (m) found.push(m[0]);
  }
  return found;
}

describe("ChatPage width breakpoint", () => {
  it("wide viewport (≥1250px): rail renders, chat column narrows to 780px, tagged progress moves out", async () => {
    renderChat(true);
    // The rail comes up once the metric resolves…
    expect(await screen.findByText(ui().railTitle)).toBeInTheDocument();
    // …the chat column switches to the narrow measure (HistoryList's
    // column + the Composer's wrappers — one boolean, all consumers)…
    expect(chatMeasures().length).toBeGreaterThan(0);
    expect(new Set(chatMeasures())).toEqual(new Set(["max-w-[780px]"]));
    // …and the tagged node-progress line leaves the CHAT COLUMN (the
    // rail's timeline owns it now — the text still exists, over there;
    // so scope the absence query to the chat column itself).
    const column = [...document.querySelectorAll("div")].find((el) =>
      el.className.includes("max-w-[780px]"),
    );
    expect(column).toBeDefined();
    await vi.waitFor(() =>
      expect(
        within(column as HTMLElement).queryByText("baseline 3/5"),
      ).toBeNull(),
    );
  });

  it("narrow viewport (<1250px): rail stays hidden even with a live drill, chat column keeps 980px", async () => {
    const { queryClient } = renderChat(false);
    // Non-vacuity: wait until the drill metric has actually resolved
    // (the drill IS live) before asserting the rail never shows.
    await vi.waitFor(() =>
      expect(queryClient.getQueryState(["activeDrill", "t1"])?.status).toBe(
        "success",
      ),
    );
    expect(screen.queryByText(ui().railTitle)).toBeNull();
    expect(chatMeasures().length).toBeGreaterThan(0);
    expect(new Set(chatMeasures())).toEqual(new Set(["max-w-[980px]"]));
    // The tagged line stays in the chat column (fallback rule).
    expect(screen.getByText("baseline 3/5")).toBeInTheDocument();
  });
});
