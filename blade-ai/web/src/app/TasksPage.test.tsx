/**
 * TasksPage: query → filter → table pipeline, plus row → detail
 * navigation.
 *
 * The page calls ``useNavigate`` (rows are links to /trace/$taskId),
 * so the tests render it inside a minimal in-memory router with a stub
 * detail route — a bare provider wrap would throw on the navigate
 * hook. The booted client is still injected through BootContext with
 * only ``listTasks`` wired; full-table integration through the real
 * route tree is covered by router.test.tsx.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
  useParams,
} from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configureI18n, type BladeClient } from "@blade-ai/core";
import { BootContext } from "./bootContext";
import { TasksPage } from "./TasksPage";
import { ui } from "../lib/uiText";

configureI18n("en");

afterEach(cleanup);

const TASKS: Record<string, unknown>[] = [
  {
    task_id: "t-1",
    status: "success",
    phase: "done",
    params: { scope: "pod", target: "cpu", action: "fullload" },
    duration_ms: 61_000,
    summary: { total_token_input: 1200, total_token_output: 300 },
    gmt_create: "2026-08-18 10:11:12",
  },
  {
    task_id: "t-2",
    status: "failed",
    phase: "inject",
    params: { scope: "node", target: "disk", action: "fill" },
    duration_ms: 500,
    summary: { total_token_input: 0, total_token_output: 0 },
    gmt_create: "2026-08-18 12:00:00",
  },
  {
    // Mid-flight task: "verifying" is an active phase, and the missing
    // duration/summary exercise the "—" fallbacks. created_at (no
    // gmt_create) covers the ISO alternate key.
    task_id: "t-3",
    status: "in_progress",
    phase: "verifying",
    params: {},
    created_at: "2026-08-18T13:00:00",
  },
];

/** Stub detail destination: renders the route param so a navigation
 *  assertion can check WHICH task the row opened. */
function DetailStub() {
  const { taskId } = useParams({ strict: false });
  return <div>DETAIL:{String(taskId)}</div>;
}

function renderTasks(listTasks: () => Promise<Record<string, unknown>>) {
  const client = { listTasks: vi.fn(listTasks) } as unknown as BladeClient;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const rootRoute = createRootRoute({
    component: () => (
      <QueryClientProvider client={queryClient}>
        <BootContext.Provider
          // TasksPage only reads ``client`` — the rest are inert
          // dummies satisfying BootContextValue.
          value={{
            client,
            activeSessionId: "s",
            resetSession: async () => {},
            switchSession: async () => {},
            langChoice: "browser",
            setLangChoice: () => {},
          }}
        >
          <Outlet />
        </BootContext.Provider>
      </QueryClientProvider>
    ),
  });
  const listRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/tasks",
    component: TasksPage,
  });
  const detailRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/trace/$taskId",
    component: DetailStub,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([listRoute, detailRoute]),
    history: createMemoryHistory({ initialEntries: ["/tasks"] }),
  });
  render(<RouterProvider router={router} />);
}

function renderTasksOk(tasks: Record<string, unknown>[]) {
  return renderTasks(() => Promise.resolve({ tasks, total: tasks.length }));
}

describe("TasksPage", () => {
  it("renders the table with derived columns", async () => {
    renderTasksOk(TASKS);

    // Fault labels prefer the joined scope-target-action form.
    expect(await screen.findByText("pod-cpu-fullload")).toBeInTheDocument();
    expect(screen.getByText("node-disk-fill")).toBeInTheDocument();
    // Status text next to its dot.
    expect(screen.getByText("success")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText("in_progress")).toBeInTheDocument();
    // Duration / tokens / created formatting.
    expect(screen.getByText("1m01s")).toBeInTheDocument();
    expect(screen.getByText("1.5k")).toBeInTheDocument();
    expect(screen.getByText("08-18 10:11")).toBeInTheDocument();
    expect(screen.getByText("08-18 13:00")).toBeInTheDocument();
    // Missing duration/summary on the mid-flight row → placeholders.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(2);
  });

  it("filters via the same passesTasksFilter rules as the TUI", async () => {
    renderTasksOk(TASKS);
    await screen.findByText("pod-cpu-fullload");

    fireEvent.click(screen.getByRole("button", { name: ui().tasksFilterFailed }));
    expect(screen.queryByText("pod-cpu-fullload")).toBeNull();
    expect(screen.getByText("node-disk-fill")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: ui().tasksFilterActive }));
    expect(screen.queryByText("node-disk-fill")).toBeNull();
    expect(screen.getByText("verifying")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: ui().tasksFilterAll }));
    expect(screen.getByText("pod-cpu-fullload")).toBeInTheDocument();
  });

  it("opens the task detail on row click", async () => {
    renderTasksOk(TASKS);
    const row = (await screen.findByText("node-disk-fill")).closest("tr");
    expect(row).not.toBeNull();
    fireEvent.click(row!);
    expect(await screen.findByText("DETAIL:t-2")).toBeInTheDocument();
  });

  it("opens the task detail on Enter from a focused row", async () => {
    renderTasksOk(TASKS);
    const row = (await screen.findByText("pod-cpu-fullload")).closest("tr");
    expect(row).not.toBeNull();
    fireEvent.keyDown(row!, { key: "Enter" });
    expect(await screen.findByText("DETAIL:t-1")).toBeInTheDocument();
  });

  it("announces rows as links", async () => {
    renderTasksOk(TASKS);
    await screen.findByText("pod-cpu-fullload");
    // role="link" is the a11y affordance that makes row-click
    // discoverable to assistive tech — one per rendered row.
    expect(screen.getAllByRole("link")).toHaveLength(3);
  });

  it("shows the empty state when no tasks exist", async () => {
    renderTasksOk([]);
    expect(await screen.findByText(ui().tasksEmpty)).toBeInTheDocument();
  });

  it("shows the filter-empty state when nothing matches", async () => {
    renderTasksOk([TASKS[0]!]); // success only — nothing failed
    await screen.findByText("pod-cpu-fullload");
    fireEvent.click(screen.getByRole("button", { name: ui().tasksFilterFailed }));
    expect(screen.getByText(ui().tasksEmptyFilter)).toBeInTheDocument();
  });

  it("shows the loading state while the query is in flight", async () => {
    renderTasks(() => new Promise(() => {}));
    // findBy, not getBy: the router's first-frame load is async, so the
    // page (and its loading state) isn't in the DOM synchronously.
    expect(await screen.findByText(ui().tasksLoading)).toBeInTheDocument();
  });

  it("shows the error state when the fetch fails", async () => {
    renderTasks(() => Promise.reject(new Error("metric endpoint down")));
    expect(
      await screen.findByText(ui().tasksLoadFailed),
    ).toBeInTheDocument();
    expect(screen.getByText(/metric endpoint down/)).toBeInTheDocument();
  });
});
