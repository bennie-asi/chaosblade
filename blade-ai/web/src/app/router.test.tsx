/**
 * Layout-level integration tests, driving the REAL route table under a
 * memory history:
 *
 * - boot flow: health probe → session create → context populate →
 *   gated app shell (rail + chat surface);
 * - the P3 load-bearing invariant: navigating away from the chat and
 *   back must NOT re-boot (one session per app lifetime) and must NOT
 *   lose the conversation;
 * - pagehide session cleanup (real unload vs bfcache freeze);
 * - the boot-failure page.
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { configureI18n } from "@blade-ai/core";
import { createAppRouter } from "./router";
import { ui } from "../lib/uiText";
import { installMockBackend, MOCK_SESSION_ID } from "../test/mockBackend";

configureI18n("en");

const realFetch = globalThis.fetch;

afterEach(() => {
  cleanup();
  globalThis.fetch = realFetch;
  localStorage.clear();
});

function renderApp(initialPath = "/") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const router = createAppRouter(
    createMemoryHistory({ initialEntries: [initialPath] }),
  );
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function submitTurn(text: string) {
  const input = screen.getByPlaceholderText(ui().inputPlaceholder);
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: "Enter" });
}

describe("router / RootLayout", () => {
  it("boots into the chat surface with cluster context in the status bar", async () => {
    installMockBackend();
    renderApp("/");

    // NOTE: the pre-boot "connecting" gate frame is not synchronously
    // observable here — RouterProvider's first paint is itself async
    // (router.load), unlike the old direct-rendered App. The gate is
    // still exercised end-to-end by the boot-failure test below.

    // Composer appears once health + createSession succeed.
    expect(
      await screen.findByPlaceholderText(ui().inputPlaceholder),
    ).toBeInTheDocument();

    // Status bar: cluster / namespace / model from the state endpoint,
    // plus the session-id prefix on the right. Scoped to <main>: the
    // session panel carries the full id only as a title attribute, so
    // an unscoped text query would already be safe — the scope stays
    // as cheap insurance.
    const main = within(screen.getByRole("main"));
    expect(main.getByText("test-cluster")).toBeInTheDocument();
    expect(main.getByText(/test-ns/)).toBeInTheDocument();
    expect(main.getByText(/test-model/)).toBeInTheDocument();
    expect(
      main.getByText(MOCK_SESSION_ID.slice(0, 8)),
    ).toBeInTheDocument();
  });

  it("boots exactly once across navigation and keeps the conversation", async () => {
    // THE p3-1 invariant: boot lives in the root layout, so leave-and-
    // return neither re-creates the session nor drops the history.
    const spy = installMockBackend();
    renderApp("/");

    await screen.findByPlaceholderText(ui().inputPlaceholder);
    submitTurn("hello drill");
    expect(await screen.findByText("hello drill")).toBeInTheDocument();

    // Chat → Tasks → Chat via the real rail links.
    fireEvent.click(screen.getByRole("link", { name: ui().navTasks }));
    expect(
      await screen.findByRole("heading", { name: ui().tasksTitle }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: ui().navChat }));
    expect(await screen.findByText("hello drill")).toBeInTheDocument();

    const createCalls = spy.mock.calls.filter(
      ([url, init]) =>
        String(url).endsWith("/api/v1/sessions") &&
        (init?.method ?? "GET") === "POST",
    );
    expect(createCalls).toHaveLength(1);
  });

  it("starts a new session from the session panel: swap id, wipe history, KEEP old", async () => {
    // THE p3-2 invariant (mock v2 semantics): the session panel's
    // "+" runs the layout's resetSession — create new →
    // HISTORY_CLEARED → SESSION_INITIALIZED — so the chat surface
    // starts clean under a fresh id without a page reload. The OLD
    // session is deliberately kept server-side: the web sidebar
    // lists every session for switch-back, unlike the TUI "/new"
    // drop-old contract. Deleting the old id here would blank the
    // sidebar back to a single row — the exact bug this pins against.
    const spy = installMockBackend();
    renderApp("/");

    await screen.findByPlaceholderText(ui().inputPlaceholder);
    submitTurn("hello drill");
    expect(await screen.findByText("hello drill")).toBeInTheDocument();
    // A typed-but-unsent draft must be wiped too — the Composer is
    // keyed by sessionId precisely so the reset remounts it.
    const input = screen.getByPlaceholderText(ui().inputPlaceholder);
    fireEvent.change(input, { target: { value: "unsent draft" } });

    const newSessionBtn = screen.getByRole("button", {
      name: ui().sessionNew,
    });
    // The gate pins the button disabled until the turn finishes
    // streaming (streamState back to idle).
    await waitFor(() => expect(newSessionBtn).toBeEnabled());
    fireEvent.click(newSessionBtn);

    // Status bar switches to the new session (sess_extra_1 → prefix).
    // Scoped to <main> — the session panel carries the id only in the
    // title attribute, never as text.
    const main = within(screen.getByRole("main"));
    expect(await main.findByText("sess_ext")).toBeInTheDocument();
    // History and draft are gone.
    await waitFor(() =>
      expect(screen.queryByText("hello drill")).toBeNull(),
    );
    expect(screen.getByPlaceholderText(ui().inputPlaceholder)).toHaveValue("");

    // Wire shape: exactly one extra POST, and NO delete for the old
    // id — the sidebar keeps it for switch-back (mock v2 semantics;
    // the TUI "/new" drop-old contract doesn't apply to web).
    const createCalls = spy.mock.calls.filter(
      ([url, init]) =>
        String(url).endsWith("/api/v1/sessions") &&
        (init?.method ?? "GET") === "POST",
    );
    expect(createCalls).toHaveLength(2);
    const deleteCall = spy.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith(`/api/v1/sessions/${MOCK_SESSION_ID}`) &&
        init?.method === "DELETE",
    );
    expect(deleteCall).toBeUndefined();
  });

  it("serves the tasks page directly at /tasks", async () => {
    installMockBackend({
      tasks: [
        {
          task_id: "t-1",
          status: "success",
          phase: "done",
          params: { scope: "pod", target: "cpu", action: "fullload" },
        },
      ],
    });
    renderApp("/tasks");

    expect(
      await screen.findByRole("heading", { name: ui().tasksTitle }),
    ).toBeInTheDocument();
    expect(await screen.findByText("pod-cpu-fullload")).toBeInTheDocument();
  });

  it("navigates from a task row to the trace detail and back", async () => {
    // THE audit-surface invariant: a task row click lands on
    // /trace/$taskId, which renders the single-task metric envelope
    // (GET /api/v1/metric/{id}) — fields the list never shows
    // (experiment uid, spans) prove the detail pipeline end-to-end.
    installMockBackend({
      tasks: [
        {
          task_id: "t-1",
          status: "success",
          phase: "done",
          params: { scope: "pod", target: "cpu", action: "fullload" },
        },
      ],
      taskDetails: {
        "t-1": {
          task_id: "t-1",
          status: "success",
          phase: "done",
          params: { scope: "pod", target: "cpu", action: "fullload" },
          target: { namespace: "cms", names: ["web-1"] },
          experiment_uid: "blade-xyz",
          spans: [
            { node_name: "inject", start_time: 1000, duration_ms: 2000 },
          ],
        },
      },
    });
    const router = renderApp("/tasks");

    const row = (await screen.findByText("pod-cpu-fullload")).closest("tr");
    fireEvent.click(row!);

    // Detail-only content, scoped to <main> (the rail is chrome).
    const main = within(screen.getByRole("main"));
    expect(await main.findByText("blade-xyz")).toBeInTheDocument();
    expect(main.getByText("inject")).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/trace/t-1");

    // Back to the list via the rail's tasks nav.
    fireEvent.click(screen.getByRole("link", { name: ui().navTasks }));
    expect(
      await screen.findByRole("heading", { name: ui().tasksTitle }),
    ).toBeInTheDocument();
  });

  it("redirects the legacy /tasks/$taskId deep link to /trace/$taskId", async () => {
    // The detail route moved; the old path stays a redirect so existing
    // bookmarks keep working.
    installMockBackend({
      tasks: [{ task_id: "t-9", status: "success", phase: "done" }],
      taskDetails: {
        "t-9": { task_id: "t-9", status: "success", phase: "done" },
      },
    });
    const router = renderApp("/tasks/t-9");

    // findByRole, not getByRole: <main> doesn't exist until the boot
    // gate clears (health + session create), which is async.
    const main = within(await screen.findByRole("main"));
    expect(await main.findByText("t-9")).toBeInTheDocument();
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/trace/t-9"),
    );
  });

  it("renders the not-found state for an unknown task id", async () => {
    installMockBackend();
    renderApp("/tasks/nope");

    const main = within(await screen.findByRole("main"));
    expect(
      await main.findByText(ui().taskDetailLoadFailed),
    ).toBeInTheDocument();
    expect(main.getByText(/Task not found: nope/)).toBeInTheDocument();
  });

  it("redirects the retired /replay page to /trace", async () => {
    // The replay page is retired (the trace page absorbed it); /replay
    // stays a bare redirect so existing bookmarks land on the audit
    // surface instead of 404ing. The recording endpoints + the core
    // /replay machinery remain — just with no web UI mounted.
    installMockBackend();
    const router = renderApp("/replay");

    const main = within(await screen.findByRole("main"));
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/trace"),
    );
    expect(await main.findByText(ui().traceEmpty)).toBeInTheDocument();
  });

  it("serves the charts page lazily at /charts", async () => {
    // THE p3-5 invariant: the route loads its page (and the recharts
    // chunk behind it) via the router's lazy import, then folds the
    // task details into both charts — the usage tick proves the
    // detail pipeline end-to-end.
    installMockBackend({
      tasks: [{ task_id: "t-1", status: "success", phase: "done" }],
      taskDetails: {
        "t-1": {
          task_id: "t-1",
          status: "success",
          phase: "done",
          model_name: "qwen-max",
          spans: [
            { node_name: "execute_loop", start_time: 1, duration_ms: 2000 },
          ],
          summary: { total_token_input: 10, total_token_output: 5 },
        },
      },
    });
    renderApp("/charts");

    const main = within(await screen.findByRole("main"));
    expect(
      await main.findByRole("heading", { name: ui().chartsTitle }),
    ).toBeInTheDocument();
    expect(main.getByText("qwen-max")).toBeInTheDocument();
  });

it("serves the settings page at /settings under the same boot", async () => {
    installMockBackend({ config: { confirmation_required: true } });
    renderApp("/settings");

    const main = within(await screen.findByRole("main"));
    expect(
      await main.findByRole("heading", { name: ui().settingsTitle }),
    ).toBeInTheDocument();
    // The page rendered the fetched row (not an empty shell) — the
    // toggle carries the seeded value.
    expect(
      main
        .getByRole("switch", { name: "confirmation_required" })
        .getAttribute("aria-checked"),
    ).toBe("true");
  });

  it("switches the interface language from the settings page", async () => {
    // THE p3-6 invariant: the settings language control drives the REAL
    // RootLayout wiring — localStorage persistence + configureI18n +
    // a whole-tree re-render — not a mocked context.
    installMockBackend();
    renderApp("/settings");

    try {
      const main = within(await screen.findByRole("main"));
      fireEvent.click(
        await main.findByRole("button", { name: "中文" }),
      );

      // The page heading re-resolves in the new language…
      await main.findByRole("heading", { name: "设置" });
      // …the choice is persisted for the next boot…
      expect(localStorage.getItem("blade_ai_lang")).toBe("zh");
      // …and chrome outside the page (the rail) followed along.
      expect(
        screen.getByRole("link", { name: "设置" }),
      ).toBeInTheDocument();
    } finally {
      // configureI18n is module-global: restore English no matter how
      // the assertions above landed, or the rest of the file reads
      // Chinese chrome.
      configureI18n("en");
    }
  });

  it("deletes the session with keepalive when the page unloads", async () => {
    // Refresh / tab-close must not leak the session into the embedded
    // server's memory: the pagehide listener fires a keepalive DELETE.
    const spy = installMockBackend();
    renderApp("/");
    await screen.findByPlaceholderText(ui().inputPlaceholder);

    fireEvent(window, new Event("pagehide"));

    await waitFor(() => {
      const deleteCall = spy.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith(`/api/v1/sessions/${MOCK_SESSION_ID}`) &&
          init?.method === "DELETE",
      );
      expect(deleteCall).toBeDefined();
      expect(deleteCall![1]?.keepalive).toBe(true);
    });
  });

  it("keeps the session when pagehide is a bfcache freeze (persisted)", async () => {
    // Back/forward navigation fires pagehide with persisted=true and
    // the page may be restored intact — deleting the session would
    // orphan it (every subsequent turn would 404).
    const spy = installMockBackend();
    renderApp("/");
    await screen.findByPlaceholderText(ui().inputPlaceholder);
    // Let the session panel's sessions query settle inside act's window —
    // otherwise its state update lands after this test's last await
    // and React warns about an unwrapped update. The entry button's
    // title carries the full session id; the status bar shows the same
    // id prefix from SESSION_INITIALIZED, so only the panel entry
    // proves the query resolved.
    await screen.findByTitle(MOCK_SESSION_ID);

    const frozen = new Event("pagehide");
    Object.defineProperty(frozen, "persisted", { value: true });
    fireEvent(window, frozen);

    // Give the (synchronously-invoked) handler a beat, then assert the
    // DELETE was never issued. fetch itself is called synchronously by
    // deleteSession, so a short sleep is a reliable negative witness.
    await new Promise((resolve) => setTimeout(resolve, 50));
    const deleteCall = spy.mock.calls.find(
      ([url, init]) =>
        String(url).endsWith(`/api/v1/sessions/${MOCK_SESSION_ID}`) &&
        init?.method === "DELETE",
    );
    expect(deleteCall).toBeUndefined();
  });

  it("shows the boot-failure page when session creation is rejected", async () => {
    installMockBackend({ failCreateSession: true });
    renderApp("/");

    expect(
      await screen.findByText(ui().bootFailedTitle),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/createSession failed: HTTP 500/),
    ).toBeInTheDocument();
    expect(screen.getByText(ui().bootFailedHint)).toBeInTheDocument();
  });
});
