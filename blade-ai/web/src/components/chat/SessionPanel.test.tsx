/**
 * SessionPanel tests — the chat route's session-history column
 * (design §6, mock col-2).
 *
 * Pins:
 *   - today / yesterday / past-7-days / earlier grouping and the
 *     ``{n} tasks · HH:MM`` entry titles (the only fields the wire
 *     carries — DATA HONESTY, see the component docstring)
 *   - the per-entry drill status dot (mock v2): live / fail / done
 *     from task_ids × listTasks, absent when the session has no
 *     tasks or the probe fails (enhancement, never a blocker)
 *   - active highlighting and click-to-switch
 *   - the two-step delete: first click runs the active-drill precheck
 *     (session state × task list) and arms, second click deletes;
 *     a session with an in-flight drill blocks inline
 *   - deleting the ACTIVE session re-homes the chat (newest remaining,
 *     or a fresh session when the list ran empty)
 *
 * The client is a hand stub (same shape as TracePage.test) — the
 * delete flow re-reads ``listSessions`` after ``deleteSession``, so
 * the stub's answers are sequenced per call.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  StoreProvider,
  configureI18n,
  type BladeClient,
  type SessionListItem,
} from "@blade-ai/core";
import { BootContext, type BootContextValue } from "../../app/bootContext";
import { ui } from "../../lib/uiText";
import { SessionPanel, useSessionPanelCollapsed } from "./SessionPanel";

configureI18n("en");

afterEach(() => {
  cleanup();
  localStorage.removeItem(useSessionPanelCollapsed.key);
  useSessionPanelCollapsed.resetForTests();
});

/** Local-wall-time "YYYY-MM-DD HH:MM:SS" — the server's created_at
 *  shape (slice-based downstream, never Date-reparsed). */
function stamp(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:00`
  );
}

function session(
  id: string,
  createdAt: string,
  taskCount: number,
): SessionListItem {
  return {
    id,
    cluster: "prod",
    namespace: "cms",
    model_name: "qwen-max",
    created_at: createdAt,
    task_count: taskCount,
  };
}

interface Harness {
  client: BladeClient;
  container: HTMLElement;
  switchSession: ReturnType<typeof vi.fn>;
  resetSession: ReturnType<typeof vi.fn>;
  listSessions: ReturnType<typeof vi.fn>;
  deleteSession: ReturnType<typeof vi.fn>;
  getSessionState: ReturnType<typeof vi.fn>;
  listTasks: ReturnType<typeof vi.fn>;
}

function renderPanel({
  sessions,
  activeId = "s1",
  sessionState = { task_ids: [] },
  sessionStates,
  tasks = [],
}: {
  sessions: SessionListItem[];
  activeId?: string;
  sessionState?: Record<string, unknown>;
  /** Per-session override for the drill-dot probe; falls back to
   *  ``sessionState`` for ids not present. */
  sessionStates?: Record<string, Record<string, unknown>>;
  tasks?: Record<string, unknown>[];
}): Harness {
  const listSessions = vi.fn().mockResolvedValue(sessions);
  const deleteSession = vi.fn().mockResolvedValue(undefined);
  const getSessionState = vi
    .fn()
    .mockImplementation((id: string) =>
      Promise.resolve(sessionStates?.[id] ?? sessionState),
    );
  const listTasks = vi.fn().mockResolvedValue({ tasks, total: tasks.length });
  const client = {
    listSessions,
    deleteSession,
    getSessionState,
    listTasks,
  } as unknown as BladeClient;
  const switchSession = vi.fn().mockResolvedValue(undefined);
  const resetSession = vi.fn().mockResolvedValue(undefined);
  const boot: BootContextValue = {
    client,
    activeSessionId: activeId,
    resetSession,
    switchSession,
    langChoice: "browser",
    setLangChoice: () => {},
  };
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <StoreProvider>
        <BootContext.Provider value={boot}>
          <SessionPanel />
        </BootContext.Provider>
      </StoreProvider>
    </QueryClientProvider>,
  );
  return {
    client,
    container: view.container,
    switchSession,
    resetSession,
    listSessions,
    deleteSession,
    getSessionState,
    listTasks,
  };
}

const NOW = new Date();
const TODAY = stamp(NOW);
const YESTERDAY = stamp(new Date(NOW.getTime() - 86_400_000));
const LONG_AGO = "2020-01-02 03:04:05";

describe("grouping + titles", () => {
  it("buckets entries into today / yesterday / earlier with task-count titles", async () => {
    renderPanel({
      sessions: [
        session("s1", TODAY, 2),
        session("s2", YESTERDAY, 1),
        session("s3", LONG_AGO, 0),
      ],
    });
    expect(await screen.findByText(ui().sessionGroupToday)).toBeInTheDocument();
    expect(screen.getByText(ui().sessionGroupYesterday)).toBeInTheDocument();
    expect(screen.getByText(ui().sessionGroupEarlier)).toBeInTheDocument();
    // Titles: task_count + clock tail (the only honest fields).
    expect(
      screen.getByText(`2 tasks · ${TODAY.slice(11, 16)}`),
    ).toBeInTheDocument();
    expect(
      screen.getByText(`1 task · ${YESTERDAY.slice(11, 16)}`),
    ).toBeInTheDocument();
    expect(screen.getByText("No tasks · 03:04")).toBeInTheDocument();
  });
});

describe("switching", () => {
  it("switches on entry click, never on the active row", async () => {
    const h = renderPanel({
      sessions: [session("s1", TODAY, 1), session("s2", TODAY, 3)],
    });
    const s2 = await screen.findByText(`3 tasks · ${TODAY.slice(11, 16)}`);
    fireEvent.click(s2);
    await vi.waitFor(() =>
      expect(h.switchSession).toHaveBeenCalledWith("s2"),
    );
    // The active row's button is disabled — no self-switch.
    const s1 = screen.getByText(`1 task · ${TODAY.slice(11, 16)}`);
    expect(s1.closest("button")).toBeDisabled();
    expect(h.switchSession).not.toHaveBeenCalledWith("s1");
  });
});

describe("delete", () => {
  it("two-step: first click prechecks and arms, second click deletes", async () => {
    const h = renderPanel({
      sessions: [session("s1", TODAY, 1), session("s2", TODAY, 3)],
    });
    // Let the drill-dot probe settle, then clear — the assertions
    // below must pin the DELETE precheck's own calls, not the probe's
    // (the probe also calls getSessionState/listTasks on mount).
    await vi.waitFor(() =>
      expect(h.getSessionState).toHaveBeenCalledTimes(2),
    );
    h.getSessionState.mockClear();
    h.listTasks.mockClear();
    // Open the ⋯ menu on the non-active row.
    const moreButtons = await screen.findAllByTitle(ui().sessionMore);
    fireEvent.click(moreButtons[1]!);
    // First click: precheck runs, item arms.
    fireEvent.click(screen.getByText(ui().sessionDelete));
    expect(await screen.findByText(ui().sessionDeleteArm)).toBeInTheDocument();
    expect(h.getSessionState).toHaveBeenCalledWith("s2");
    expect(h.listTasks).toHaveBeenCalled();
    expect(h.deleteSession).not.toHaveBeenCalled();
    // Second click: the delete lands.
    fireEvent.click(screen.getByText(ui().sessionDeleteArm));
    await vi.waitFor(() => expect(h.deleteSession).toHaveBeenCalledWith("s2"));
  });

  it("blocks inline when the session holds an in-flight drill", async () => {
    renderPanel({
      sessions: [session("s1", TODAY, 1), session("s2", TODAY, 3)],
      sessionState: { task_ids: ["t1"] },
      tasks: [{ task_id: "t1", phase: "executing", status: "in_progress" }],
    });
    const moreButtons = await screen.findAllByTitle(ui().sessionMore);
    fireEvent.click(moreButtons[1]!);
    fireEvent.click(screen.getByText(ui().sessionDelete));
    expect(
      await screen.findByText(ui().sessionDeleteBlocked),
    ).toBeInTheDocument();
  });

  it("deleting the ACTIVE session re-homes to the newest remaining one", async () => {
    const rest = [session("s2", TODAY, 3)];
    const h = renderPanel({
      sessions: [session("s1", TODAY, 1), ...rest],
    });
    // After the delete, the re-read list no longer carries s1.
    h.listSessions.mockResolvedValue(rest);
    const moreButtons = await screen.findAllByTitle(ui().sessionMore);
    fireEvent.click(moreButtons[0]!);
    fireEvent.click(screen.getByText(ui().sessionDelete));
    fireEvent.click(await screen.findByText(ui().sessionDeleteArm));
    await vi.waitFor(() =>
      expect(h.switchSession).toHaveBeenCalledWith("s2"),
    );
  });

  it("deleting the LAST session falls back to a fresh one", async () => {
    const only = [session("s1", TODAY, 1)];
    const h = renderPanel({ sessions: only });
    h.listSessions.mockResolvedValue([]);
    const moreButtons = await screen.findAllByTitle(ui().sessionMore);
    fireEvent.click(moreButtons[0]!);
    fireEvent.click(screen.getByText(ui().sessionDelete));
    fireEvent.click(await screen.findByText(ui().sessionDeleteArm));
    await vi.waitFor(() => expect(h.resetSession).toHaveBeenCalled());
  });
});

describe("mock v2 alignment increment", () => {
  it("buckets a 3-day-old session into the past-7-days group", async () => {
    renderPanel({
      sessions: [
        session("s1", TODAY, 1),
        session("s2", stamp(new Date(NOW.getTime() - 3 * 86_400_000)), 2),
      ],
    });
    expect(await screen.findByText(ui().sessionGroupWeek)).toBeInTheDocument();
    // s2 is neither yesterday nor long ago — the neighbours stay shut.
    expect(screen.queryByText(ui().sessionGroupYesterday)).toBeNull();
    expect(screen.queryByText(ui().sessionGroupEarlier)).toBeNull();
  });

  it("renders the drill dot per session: live / fail / done, none without tasks", async () => {
    const { container } = renderPanel({
      sessions: [
        session("s-live", TODAY, 1),
        session("s-fail", TODAY, 1),
        session("s-done", TODAY, 1),
        session("s-none", TODAY, 0),
      ],
      sessionStates: {
        "s-live": { task_ids: ["t1"] },
        "s-fail": { task_ids: ["t2"] },
        "s-done": { task_ids: ["t3"] },
        "s-none": { task_ids: [] },
      },
      tasks: [
        { task_id: "t1", phase: "executing", status: "in_progress" },
        { task_id: "t2", phase: "done", status: "failed" },
        { task_id: "t3", phase: "done", status: "success" },
      ],
    });
    // Precedence: live > fail > done; a task-less session stays bare.
    await vi.waitFor(() =>
      expect(
        container.querySelectorAll('[data-drill-status="live"]'),
      ).toHaveLength(1),
    );
    expect(
      container.querySelectorAll('[data-drill-status="fail"]'),
    ).toHaveLength(1);
    expect(
      container.querySelectorAll('[data-drill-status="done"]'),
    ).toHaveLength(1);
    expect(container.querySelectorAll("[data-drill-status]")).toHaveLength(3);
    expect(screen.getByTitle(ui().sessionDotLive)).toBeInTheDocument();
    expect(screen.getByTitle(ui().sessionDotFail)).toBeInTheDocument();
    expect(screen.getByTitle(ui().sessionDotDone)).toBeInTheDocument();
  });

  it("collapsed slim strip: the drill-dot probe stays off until expanded", async () => {
    localStorage.setItem(useSessionPanelCollapsed.key, "1");
    const h = renderPanel({ sessions: [session("s1", TODAY, 1)] });
    // Slim strip renders and the session list still loads (kept warm
    // for a snappy expand), but the dot probe is disabled — it would
    // be N+1 requests per tick for dots nobody can see.
    expect(
      await screen.findByTitle(ui().sessionPanelExpand),
    ).toBeInTheDocument();
    await vi.waitFor(() => expect(h.listSessions).toHaveBeenCalled());
    expect(h.getSessionState).not.toHaveBeenCalled();
    expect(h.listTasks).not.toHaveBeenCalled();
  });

  it("a failed dot probe leaves the list intact and dot-free", async () => {
    const h = renderPanel({ sessions: [session("s1", TODAY, 1)] });
    h.listTasks.mockRejectedValue(new Error("metric down"));
    expect(
      await screen.findByText(`1 task · ${TODAY.slice(11, 16)}`),
    ).toBeInTheDocument();
    // Give the rejected probe a beat to settle — the dot must NOT appear.
    await vi.waitFor(() => expect(h.listTasks).toHaveBeenCalled());
    expect(h.container.querySelectorAll("[data-drill-status]")).toHaveLength(0);
  });
});
