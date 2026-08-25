/**
 * ProgressRail tests — the right progress rail (design §7.2 + mock
 * v2 column 4).
 *
 * Pins:
 *   - visibility rule: no candidate task / terminal drill → the rail
 *     renders nothing; non-terminal task_state or the client-side
 *     in-flight fallback → it renders
 *   - the fixed panel order's content: summary (fault/target/uid),
 *     countdown (anchored on the earliest execute_loop progress ts),
 *     params card (CRI four-tuple + assembled blade command), safety
 *     snapshot (real safety_status / feasibility_report fields)
 *   - collapse round-trip with localStorage persistence
 *   - the copy-command feedback and the DAG overlay entry
 *   - buildBladeCommand assembly (pure function)
 *
 * The countdown is asserted by FORMAT (mm:ss), not an exact value —
 * fake timers + React Query intervals are a flaky combo (same
 * rationale as TracePage.test).
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  StoreProvider,
  configureI18n,
  type BladeClient,
  type HistoryItem,
} from "@blade-ai/core";
import { ui } from "../../lib/uiText";
import { ProgressRail, buildBladeCommand } from "./ProgressRail";
import { useRailCollapsed } from "./railStore";

configureI18n("en");

afterEach(() => {
  cleanup();
  localStorage.removeItem(useRailCollapsed.key);
  useRailCollapsed.resetForTests();
  vi.unstubAllGlobals();
});

const LIVE_METRIC: Record<string, unknown> = {
  task_id: "t1",
  task_state: "injected",
  status: "in_progress",
  phase: "executing",
  safety_status: "passed",
  safety_reason: "target whitelisted",
  experiment_uid: "7f3a9c21e0b4abcd",
  gmt_create: "2026-08-21 14:32:07",
  fault_spec: {
    namespace: "default",
    scope: "pod",
    names: ["web-1", "web-2", "web-3"],
    labels: { app: "payment-service" },
    fault_target: "cpu",
    fault_action: "fullload",
    params: { "cpu-percent": "80" },
    params_flags: [],
    duration_seconds: 300,
  },
  feasibility_report: {
    severity: "ok",
    message: "headroom 12% → 80%",
  },
};

function clientWith(
  getTaskMetric: () => Promise<Record<string, unknown>>,
): BladeClient {
  return { getTaskMetric } as unknown as BladeClient;
}

function renderRail(
  client: BladeClient,
  initial: Parameters<typeof StoreProvider>[0]["initial"] = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <StoreProvider initial={initial}>
        <ProgressRail client={client} />
      </StoreProvider>
    </QueryClientProvider>,
  );
}

describe("visibility", () => {
  it("renders nothing when the session has no candidate task", () => {
    const { container } = renderRail(clientWith(async () => LIVE_METRIC));
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing once the metric reports a terminal state and no turn is in flight", async () => {
    const { container } = renderRail(
      clientWith(async () => ({ ...LIVE_METRIC, task_state: "recovered" })),
      { lastTaskId: "t1" },
    );
    // The rail lingers until the first poll resolves, then disappears.
    await vi.waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("renders while task_state is non-terminal", async () => {
    renderRail(clientWith(async () => LIVE_METRIC), { lastTaskId: "t1" });
    expect(await screen.findByText(ui().railTitle)).toBeInTheDocument();
    expect(screen.getByText(ui().railLive)).toBeInTheDocument();
  });

  it("falls back to the client-side in-flight signal while the metric hasn't landed", () => {
    // getTaskMetric never resolves — the live phase stepper alone
    // must still bring the rail up.
    renderRail(clientWith(() => new Promise(() => {})), {
      lastTaskId: "t1",
      currentPhaseStepper: {
        steps: [{ id: "inject", status: "in_progress" }],
      },
    });
    expect(screen.getByText(ui().railTitle)).toBeInTheDocument();
  });
});

describe("panels", () => {
  const ANCHOR: HistoryItem = {
    kind: "log",
    id: "n1",
    level: "info",
    text: "blade create issued",
    tag: "execute_loop",
    ts: Date.now() - 60_000,
  };

  function renderLive() {
    return renderRail(clientWith(async () => LIVE_METRIC), {
      lastTaskId: "t1",
      history: [ANCHOR],
    });
  }

  it("summary card: fault line, target summary, uid + wall time", async () => {
    await renderLive();
    expect(await screen.findByText("cpu fullload")).toBeInTheDocument();
    expect(
      screen.getByText("app=payment-service · default · × 3 pod"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/uid 7f3a9c21e0b4 · \d{2}:\d{2}:\d{2} approved/),
    ).toBeInTheDocument();
  });

  it("countdown renders mm:ss while injected with a duration and an anchor", async () => {
    await renderLive();
    expect(await screen.findByText(ui().railCountdown)).toBeInTheDocument();
    expect(screen.getByText(/^\d{2}:\d{2}$/)).toBeInTheDocument();
  });

  it("no countdown without an execute_loop anchor", async () => {
    renderRail(clientWith(async () => LIVE_METRIC), { lastTaskId: "t1" });
    expect(await screen.findByText(ui().railTitle)).toBeInTheDocument();
    expect(screen.queryByText(ui().railCountdown)).toBeNull();
    // Summary falls back to the gmt_create wall time with NO approved
    // suffix — a creation time is not an approval.
    expect(
      screen.getByText(/uid 7f3a9c21e0b4 · 14:32:07/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/approved/)).toBeNull();
  });

  it("anchor is scoped to the latest turn — an earlier drill never leaks in", async () => {
    // Same session, second drill: history still carries drill A's
    // execute_loop line. Anchoring there would drain the countdown
    // (300s duration vs a 1h-old anchor) and mislabel the approved
    // time; the anchor must start looking after the LAST user turn.
    const OLD: HistoryItem = {
      kind: "log",
      id: "n0",
      level: "info",
      text: "drill A issued",
      tag: "execute_loop",
      ts: Date.now() - 3_600_000,
    };
    const USER: HistoryItem = { kind: "user", id: "u1", text: "inject again" };
    const FRESH: HistoryItem = { ...ANCHOR, id: "n2" };
    renderRail(clientWith(async () => LIVE_METRIC), {
      lastTaskId: "t1",
      history: [OLD, USER, FRESH],
    });
    // Survives only when anchored at FRESH (remaining 240s, not <0).
    expect(await screen.findByText(ui().railCountdown)).toBeInTheDocument();
  });

  it("params card: CRI four-tuple rows + the assembled blade command", async () => {
    await renderLive();
    expect(await screen.findByText(ui().railParamsTitle)).toBeInTheDocument();
    expect(screen.getByText("pod / cpu")).toBeInTheDocument();
    expect(screen.getByText("fullload")).toBeInTheDocument();
    expect(screen.getByText("80")).toBeInTheDocument();
    expect(
      screen.getByText(`300s (${ui().railDurationAuto})`),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "blade create k8s pod-cpu fullload --cpu-percent 80 --namespace default --labels app=payment-service --timeout 300",
      ),
    ).toBeInTheDocument();
  });

  it("safety card: real safety_status + feasibility_report fields", async () => {
    await renderLive();
    expect(await screen.findByText(ui().railSafetyTitle)).toBeInTheDocument();
    expect(screen.getByText(/safety passed/)).toBeInTheDocument();
    expect(screen.getByText(/target whitelisted/)).toBeInTheDocument();
    expect(screen.getByText(/feasibility ok/)).toBeInTheDocument();
    expect(screen.getByText(/headroom 12% → 80%/)).toBeInTheDocument();
  });

  it("timeline panel hosts tagged node-progress lines", async () => {
    await renderLive();
    expect(await screen.findByText("blade create issued")).toBeInTheDocument();
  });
});

describe("chrome", () => {
  it("collapses to a slim strip and expands back, persisting the choice", async () => {
    renderRail(clientWith(async () => LIVE_METRIC), { lastTaskId: "t1" });
    expect(await screen.findByText(ui().railTitle)).toBeInTheDocument();

    fireEvent.click(screen.getByTitle(ui().railCollapse));
    expect(localStorage.getItem(useRailCollapsed.key)).toBe("1");
    expect(screen.queryByText(ui().railTitle)).toBeNull();

    fireEvent.click(screen.getByTitle(ui().railExpand));
    expect(localStorage.getItem(useRailCollapsed.key)).toBe("0");
    expect(await screen.findByText(ui().railTitle)).toBeInTheDocument();
  });

  it("copy button writes the blade command and flips to the copied label", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    renderRail(clientWith(async () => LIVE_METRIC), { lastTaskId: "t1" });
    fireEvent.click(await screen.findByTitle(ui().railCopy));
    expect(writeText).toHaveBeenCalledWith(
      "blade create k8s pod-cpu fullload --cpu-percent 80 --namespace default --labels app=payment-service --timeout 300",
    );
    expect(await screen.findByTitle(ui().railCopied)).toBeInTheDocument();
  });

  it("the graph entry opens the DAG overlay and Escape dismisses it", async () => {
    renderRail(clientWith(async () => LIVE_METRIC), { lastTaskId: "t1" });
    fireEvent.click(await screen.findByText(ui().railViewDag));
    expect(
      await screen.findByRole("dialog", { name: ui().dagPanelTitle }),
    ).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("buildBladeCommand", () => {
  it("assembles params, flags, labels and timeout", () => {
    expect(
      buildBladeCommand({
        scope: "pod",
        fault_target: "network",
        fault_action: "delay",
        params: { time: "3000", interface: "eth0" },
        params_flags: ["force"],
        namespace: "cms",
        labels: { app: "web", tier: "fe" },
        duration_seconds: 120,
      }),
    ).toBe(
      "blade create k8s pod-network delay --time 3000 --interface eth0 --force --namespace cms --labels app=web,tier=fe --timeout 120",
    );
  });

  it("degrades to the bare prefix for an empty spec", () => {
    expect(buildBladeCommand({})).toBe("blade create k8s");
  });
});
