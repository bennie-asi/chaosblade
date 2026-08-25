/**
 * ChartsPage: the newest-8 detail batch folded into gantt rows (phase
 * aggregation, short ids, 404-tolerant rows) and the by-model usage
 * aggregation, plus the empty and error states.
 *
 * Rendered WITHOUT a router (the page has no Link/useParams); the
 * client is a partial fake (listTasks / getTaskMetric). Charts use
 * fixed sizes, so recharts
 * renders synchronously inside jsdom (a ResponsiveContainer would
 * see zero width and render nothing).
 *
 * Fixture ids use a REALISTIC uuid shape ("task-aaaaaaaa-…"): the
 * page's shortId takes the first 8 chars after the "task-" prefix, so
 * a 7-char first segment would make the tick "aaaaaaa-" (trailing
 * hyphen) and every exact text assertion silently miss.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { configureI18n, type BladeClient } from "@blade-ai/core";
import { BootContext } from "./bootContext";
import { ChartsPage, fetchCharts } from "./ChartsPage";
import { ui } from "../lib/uiText";

configureI18n("en");

afterEach(() => {
  cleanup();
});

/** Task A: one span per gantt segment (incl. a plan_builder → prep and
 *  a recover span whose name ALSO contains "verif" — recover must win
 *  the matcher order). Named model. */
const DETAIL_A = {
  task_id: "task-aaa11111-2222",
  model_name: "qwen-max",
  spans: [
    { node_name: "plan_builder", start_time: 1, duration_ms: 300 },
    { node_name: "execute_loop", start_time: 2, duration_ms: 2000 },
    { node_name: "verifier_loop", start_time: 4, duration_ms: 1000 },
    { node_name: "recover_verifier_loop", start_time: 6, duration_ms: 500 },
  ],
  summary: { total_token_input: 1200, total_token_output: 300 },
};

/** Task B: se_detect (verify, unlike the inject-side se_snapshot) and
 *  an empty model_name → the unknown-model bucket. */
const DETAIL_B = {
  task_id: "task-bbb33333-4444",
  model_name: "",
  spans: [
    { node_name: "plan_builder", start_time: 1, duration_ms: 100 },
    { node_name: "execute_loop", start_time: 2, duration_ms: 4000 },
    { node_name: "se_detect", start_time: 9, duration_ms: 250 },
  ],
  summary: { total_token_input: 800, total_token_output: 200 },
};

interface FakeClientOpts {
  tasks?: Record<string, unknown>[];
  detailsById?: Record<string, Record<string, unknown>>;
  failList?: boolean;
}

function renderPage(opts: FakeClientOpts = {}) {
  const client = {
    listTasks: opts.failList
      ? vi.fn(async () => {
          throw new Error("listTasks failed: HTTP 500");
        })
      : vi.fn(async () => ({
          tasks: opts.tasks ?? [],
          total: (opts.tasks ?? []).length,
        })),
    getTaskMetric: vi.fn(async (taskId: string) => {
      const d = opts.detailsById?.[taskId];
      if (!d) {
        throw new Error(`getTaskMetric: Task not found: ${taskId}`);
      }
      return d;
    }),
  } as unknown as BladeClient;
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <BootContext.Provider
        value={{
                  client,
                  activeSessionId: "s",
                  resetSession: async () => {},
                  switchSession: async () => {},
                  langChoice: "browser",
                  setLangChoice: () => {},
                }}
      >
        <ChartsPage />
      </BootContext.Provider>
    </QueryClientProvider>,
  );
}

describe("fetchCharts aggregation", () => {
  /** Numeric truth: DOM assertions alone cannot catch a misclassified
   *  phase (the bars render regardless) — assert the folded values. */
  function fakeClient(opts: FakeClientOpts = {}) {
    return {
      listTasks: async () => ({
        tasks: opts.tasks ?? [],
        total: (opts.tasks ?? []).length,
      }),
      getTaskMetric: async (taskId: string) => {
        const d = opts.detailsById?.[taskId];
        if (!d) throw new Error(`getTaskMetric: Task not found: ${taskId}`);
        return d;
      },
    } as unknown as BladeClient;
  }

  it("folds spans into the four gantt segments by node name", async () => {
    const data = await fetchCharts(
      fakeClient({
        tasks: [
          { task_id: "task-aaa11111-2222" },
          { task_id: "task-bbb33333-4444" },
          { task_id: "task-ccc" },
        ],
        detailsById: {
          "task-aaa11111-2222": DETAIL_A,
          "task-bbb33333-4444": DETAIL_B,
        },
      }),
    );

    // DETAIL_A: plan_builder→prep, execute_loop→inject, verifier_loop→
    // verify, recover_verifier_loop→recover (recover wins over "verif").
    // DETAIL_B: se_detect→verify (unlike inject-side se_snapshot).
    // The 404'd task-ccc drops out entirely.
    expect(data.gantt).toEqual([
      { id: "aaa11111", prep: 300, inject: 2000, verify: 1000, recover: 500 },
      { id: "bbb33333", prep: 100, inject: 4000, verify: 250, recover: 0 },
    ]);
  });

  it("maps every registered graph.py node exactly once", async () => {
    // One 1ms span per node name across ALL 17 with_phase_events
    // registrations — pins the PHASE_OF table EXHAUSTIVELY: a typo'd
    // entry silently degrades to prep (the bars render fine either
    // way, so numeric-by-fixture tests above cannot see it), and a
    // node added in graph.py without updating the table shifts
    // prep's count and fails here instead of skewing the chart.
    const NODES = [
      "intent_clarification", "plan_builder", "intent_confirm",
      "safety_check", "confirmation_gate", "terminal_reports",
      "preplan_probe", "batch_setup", "agent_loop", "baseline_capture",
      "se_snapshot", "execute_loop",
      "verifier_loop", "finalize_verification", "se_detect",
      "recover_verifier_loop", "finalize_recover_verification",
    ];
    const data = await fetchCharts(
      fakeClient({
        tasks: [{ task_id: "task-allnodes0-0000" }],
        detailsById: {
          "task-allnodes0-0000": {
            task_id: "task-allnodes0-0000",
            model_name: "m",
            spans: NODES.map((n, i) => ({
              node_name: n,
              start_time: i,
              duration_ms: 1,
            })),
            summary: { total_token_input: 0, total_token_output: 0 },
          },
        },
      }),
    );

    expect(data.gantt).toEqual([
      { id: "allnodes", prep: 6, inject: 6, verify: 3, recover: 2 },
    ]);
  });

  it("aggregates tokens by model with an unknown-model bucket, sorted", async () => {
    const data = await fetchCharts(
      fakeClient({
        tasks: [{ task_id: "task-aaa11111-2222" }, { task_id: "task-bbb33333-4444" }],
        detailsById: {
          "task-aaa11111-2222": DETAIL_A,
          "task-bbb33333-4444": DETAIL_B,
        },
      }),
    );

    expect(data.usage).toEqual([
      { model: "qwen-max", input: 1200, output: 300 },
      { model: ui().chartsModelUnknown, input: 800, output: 200 },
    ]);
  });

  it("returns empty charts when the list is empty", async () => {
    const data = await fetchCharts(fakeClient());
    expect(data).toEqual({ gantt: [], usage: [] });
  });
});

describe("ChartsPage", () => {
  it("renders gantt rows with phase aggregation and usage by model", async () => {
    renderPage({
      tasks: [
        { task_id: "task-aaa11111-2222" },
        { task_id: "task-bbb33333-4444" },
        { task_id: "task-ccc" }, // detail 404 → row dropped, page stands
      ],
      detailsById: {
        "task-aaa11111-2222": DETAIL_A,
        "task-bbb33333-4444": DETAIL_B,
      },
    });

    // Gantt Y ticks: short ids; the 404'd task never appears.
    expect(await screen.findByText("aaa11111")).toBeInTheDocument();
    expect(screen.getByText("bbb33333")).toBeInTheDocument();
    expect(screen.queryByText("ccc")).toBeNull();

    // Legends: four phases (dot + text pairs) and the token pair.
    expect(screen.getByText(ui().chartsPhasePrep)).toBeInTheDocument();
    expect(screen.getByText(ui().chartsPhaseInject)).toBeInTheDocument();
    expect(screen.getByText(ui().chartsPhaseVerify)).toBeInTheDocument();
    expect(screen.getByText(ui().chartsPhaseRecover)).toBeInTheDocument();
    expect(screen.getByText(ui().chartsTokensIn)).toBeInTheDocument();
    expect(screen.getByText(ui().chartsTokensOut)).toBeInTheDocument();

    // Usage Y ticks: the named model and the unknown-model bucket.
    expect(screen.getByText("qwen-max")).toBeInTheDocument();
    expect(screen.getByText(ui().chartsModelUnknown)).toBeInTheDocument();
  });

  it("shows the empty state when there are no tasks", async () => {
    renderPage();

    expect(await screen.findByText(ui().chartsEmpty)).toBeInTheDocument();
  });

  it("shows the load error when the task list fails", async () => {
    renderPage({ failList: true });

    expect(await screen.findByText(ui().chartsLoadFailed)).toBeInTheDocument();
    expect(screen.getByText(/listTasks failed: HTTP 500/)).toBeInTheDocument();
  });
});
