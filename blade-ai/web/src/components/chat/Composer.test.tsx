/**
 * Composer: natural-language turn submission (mock SSE stream →
 * history) and the slash-dispatch contract (/clear wipes history,
 * unknown commands echo + warn instead of reaching the agent).
 *
 * The store, reducer, command registry and HistoryList are all real —
 * only the transport is stubbed.
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AppState, HistoryItem } from "@blade-ai/core";
import { BladeClient, StoreProvider, configureI18n } from "@blade-ai/core";
import { Composer } from "./Composer";
import { HistoryList } from "./HistoryList";
import { ui } from "../../lib/uiText";
import { installMockBackend, MOCK_SESSION_ID } from "../../test/mockBackend";

configureI18n("en");

const realFetch = globalThis.fetch;

afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
});

function renderChat(
  opts: {
    turnEvents?: unknown[];
    history?: HistoryItem[];
    initial?: Partial<AppState>;
    /** Keep the /turn SSE stream open until abort — a REAL in-flight
     *  turn, required for the cancel tests (useStream's cancelTurn
     *  gates on its abortRef, so a forged streamState would no-op). */
    hangTurn?: boolean;
    /** draftSeed passed straight through to the Composer (hero chip
     *  injection path). */
    draftSeed?: { text: string; seq: number };
  } = {},
) {
  const spy = installMockBackend({
    turnEvents: opts.turnEvents,
    hangTurn: opts.hangTurn,
  });
  const client = new BladeClient("");
  render(
    <StoreProvider
      initial={{
        ...(opts.history ? { history: opts.history } : {}),
        ...opts.initial,
      }}
    >
      <HistoryList />
      <Composer
        client={client}
        sessionId={MOCK_SESSION_ID}
        draftSeed={opts.draftSeed}
      />
    </StoreProvider>,
  );
  return spy;
}

function typeAndEnter(text: string) {
  const textarea = screen.getByPlaceholderText(ui().inputPlaceholder);
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.keyDown(textarea, { key: "Enter" });
}

describe("Composer", () => {
  it("submits a NL turn and renders the streamed agent reply", async () => {
    renderChat({
      turnEvents: [
        { type: "token", content: "pong from agent" },
        { type: "done" },
      ],
    });
    typeAndEnter("ping the pod");

    // The user's own line echoes immediately; the agent reply lands
    // once the SSE stream pumps token → done.
    expect(await screen.findByText("ping the pod")).toBeInTheDocument();
    expect(await screen.findByText("pong from agent")).toBeInTheDocument();
  });

  it("keeps the send button disabled while the input is empty", () => {
    renderChat();
    expect(
      screen.getByRole("button", { name: ui().send }),
    ).toBeDisabled();
  });

  it("/clear wipes committed history", async () => {
    renderChat({
      history: [{ kind: "user", id: "u1", text: "stale message" }],
    });
    expect(screen.getByText("stale message")).toBeInTheDocument();

    typeAndEnter("/clear");

    await waitFor(() => {
      expect(screen.queryByText("stale message")).not.toBeInTheDocument();
    });
    // The synthetic echo of the /clear line itself is wiped too.
    expect(screen.queryByText("/clear")).not.toBeInTheDocument();
  });

  it("/exit leaves a visible hint (browsers silently ignore window.close)", async () => {
    // Stub window.close: jsdom actually destroys its document on close()
    // (unlike real browsers, which no-op for non-script-opened tabs),
    // which would nuke the shared test environment.
    const realClose = window.close;
    const closeSpy = vi.fn();
    window.close = closeSpy;
    try {
      renderChat();
      typeAndEnter("/exit");

      // The typed line echoes, and instead of a dead no-op the user
      // gets a system note telling them how to actually end the session.
      expect(await screen.findByText("/exit")).toBeInTheDocument();
      expect(await screen.findByText(ui().exitHint)).toBeInTheDocument();
      expect(closeSpy).toHaveBeenCalled();
    } finally {
      window.close = realClose;
    }
  });

  it("an unknown slash command echoes the line and logs a warning", async () => {
    renderChat();
    typeAndEnter("/nonsensecmd");

    expect(await screen.findByText("/nonsensecmd")).toBeInTheDocument();
    expect(
      await screen.findByText("unknown command: /nonsensecmd — try /help"),
    ).toBeInTheDocument();
  });

  it("shows a Stop button while busy; clicking it POSTs to /cancel", async () => {
    const spy = renderChat({ hangTurn: true });
    typeAndEnter("stress the cpu");
    // The Stop button only renders once the turn is genuinely in
    // flight (streamState=responding — and, per useStream's sync
    // order, abortRef is already set by then).
    const stopBtn = await screen.findByRole("button", {
      name: new RegExp(ui().stop),
    });
    fireEvent.click(stopBtn);
    await waitFor(() => {
      const call = spy.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith(
            `/api/v1/sessions/${MOCK_SESSION_ID}/cancel`,
          ) && init?.method === "POST",
      );
      expect(call).toBeDefined();
    });
  });

  it("Esc while busy cancels the in-flight turn", async () => {
    const spy = renderChat({ hangTurn: true });
    typeAndEnter("stress the cpu");
    await screen.findByRole("button", { name: new RegExp(ui().stop) });
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => {
      expect(
        spy.mock.calls.some(
          ([url, init]) =>
            String(url).endsWith("/cancel") && init?.method === "POST",
        ),
      ).toBe(true);
    });
  });

  it("Esc closes an open palette WITHOUT cancelling the turn", async () => {
    // A real in-flight turn: if Composer's Esc listener failed to
    // step aside, cancelTurn would genuinely POST /cancel here and
    // the final assertion would fail (a forged streamState would
    // no-op the cancel and silently pass — a false negative).
    const spy = renderChat({ hangTurn: true });
    typeAndEnter("stress the cpu");
    await screen.findByRole("button", { name: new RegExp(ui().stop) });
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const paletteInput = await screen.findByPlaceholderText(
      ui().palettePlaceholder,
    );
    fireEvent.keyDown(paletteInput, { key: "Escape" });
    await waitFor(() => {
      expect(
        screen.queryByPlaceholderText(ui().palettePlaceholder),
      ).not.toBeInTheDocument();
    });
    // The palette claimed that keystroke — no cancel POST went out.
    expect(
      spy.mock.calls.some(([url]) => String(url).endsWith("/cancel")),
    ).toBe(false);
  });

  it("consumes pendingDecision: resolveConfirm POSTs to /interrupt", async () => {
    const spy = renderChat({
      initial: {
        pendingDecision: { taskId: "T7", answer: "approved" },
      },
    });

    await waitFor(() => {
      const call = spy.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith(
            `/api/v1/sessions/${MOCK_SESSION_ID}/interrupt`,
          ) && init?.method === "POST",
      );
      expect(call).toBeDefined();
      expect(JSON.parse(String(call![1]?.body))).toEqual({
        interrupt_id: "T7",
        answer: "approved",
      });
    });
  });

  it("feedback decisions fire a follow-up turn after closing the gate", async () => {
    const spy = renderChat({
      initial: {
        pendingDecision: {
          taskId: "T8",
          answer: "rejected",
          feedback: "use 50% instead",
        },
      },
    });

    await waitFor(() => {
      const interrupt = spy.mock.calls.findIndex(
        ([url, init]) =>
          String(url).endsWith("/interrupt") && init?.method === "POST",
      );
      const turn = spy.mock.calls.findIndex(
        ([url, init]) =>
          String(url).endsWith("/turn") && init?.method === "POST",
      );
      expect(interrupt).toBeGreaterThanOrEqual(0);
      expect(turn).toBeGreaterThanOrEqual(0);
      // Two-step contract: gate closes FIRST, then the feedback text
      // fires as a fresh user turn.
      expect(turn).toBeGreaterThan(interrupt);
      expect(JSON.parse(String(spy.mock.calls[interrupt]![1]?.body))).toEqual({
        interrupt_id: "T8",
        answer: "rejected",
      });
      expect(
        JSON.parse(String(spy.mock.calls[turn]![1]?.body)).input,
      ).toBe("use 50% instead");
    });
  });
});

describe("Composer draftSeed (hero chip injection)", () => {
  it("fills the draft on a seeded chip text (fill, never send)", async () => {
    const spy = renderChat({ draftSeed: { text: "inject cpu fullload", seq: 1 } });
    const textarea = screen.getByPlaceholderText(ui().inputPlaceholder);
    await waitFor(() => expect(textarea).toHaveValue("inject cpu fullload"));
    // Fill-only contract: no /turn POST fired by the seed itself —
    // sending stays the user's explicit Enter.
    const turnCalls = spy.mock.calls.filter(
      ([url, init]) =>
        String(url).endsWith("/turn") && init?.method === "POST",
    );
    expect(turnCalls).toHaveLength(0);
  });

  it("seq=0 seed is inert (boot render, nothing to inject)", () => {
    renderChat({ draftSeed: { text: "ignored", seq: 0 } });
    expect(screen.getByPlaceholderText(ui().inputPlaceholder)).toHaveValue("");
  });
});
