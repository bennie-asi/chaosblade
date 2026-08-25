/**
 * Charts route — the two Dashboard visualizations from design §7.3
 * (演练时间线 gantt + Token 用量 by model), both fed by ONE batch of
 * task details, with recharts kept out of the first-paint bundle by
 * the route-level lazy import in router.tsx (design §7.4: 图表库路由
 * 级懒加载).
 *
 * Data surface: the metric LIST endpoint carries per-task summaries
 * (token counts) but neither spans nor model_name — only the DETAIL
 * endpoint does. So one query lists tasks, takes the newest 8, and
 * fetches their details in parallel (allSettled: a 404 on one row —
 * e.g. a task archived between list and detail — drops that row
 * without failing the whole page).
 *
 * Phase aggregation maps span node names onto the four stacked gantt
 * segments via an EXPLICIT table mirroring graph.py's with_phase_events
 * registrations (the authoritative source). A substring-regex matcher
 * was considered and rejected: both designs land unknown nodes in
 * "prep", but only the regex adds a second failure mode —
 * misclassifying a FUTURE node whose name happens to contain "verif"
 * or "execute" — and makes correctness depend on rule ORDER
 * (recover_verifier_loop contains "verif", so recover would have to be
 * judged first). The table has neither hazard; adding a node in
 * graph.py simply means adding one line here.
 *
 * Charts use FIXED width/height instead of ResponsiveContainer:
 * jsdom reports zero-width containers and renders nothing, so a
 * responsive wrapper would leave the page untestable; the page wraps
 * each chart in overflow-x-auto for narrow viewports instead.
 * Animations are disabled on every Bar for the same reason —
 * recharts' default entrance animation starts from an empty first
 * frame, which never advances under jsdom (no rAF-driven repaints),
 * leaving the bars unrendered in tests. A static dashboard
 * has no business animating anyway.
 *
 * Forge palette discipline (design §7.4): one warm hue (forge-accent)
 * plus neutral grey layers and the success green — fills reference the
 * @theme CSS variables (single colour source), legends are hand-drawn
 * dot + text pairs, never filled pills.
 */
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { BladeClient } from "@blade-ai/core";
import { useBoot } from "./bootContext";
import { ui } from "../lib/uiText";
import { formatDuration } from "../lib/format";

/** Tasks per gantt/usage batch. Newest first (the list endpoint's
 * order); 8 keeps the detail fan-out and the chart readable. */
const CHART_TASKS = 8;
const ROW_HEIGHT = 30;
const AXIS_EXTRA = 56;
const CHART_WIDTH = 760;

type PhaseKey = "prep" | "inject" | "verify" | "recover";

interface GanttRow {
  id: string;
  prep: number;
  inject: number;
  verify: number;
  recover: number;
}

interface UsageRow {
  model: string;
  input: number;
  output: number;
}

interface ChartsData {
  gantt: GanttRow[];
  usage: UsageRow[];
}

/** Span node name → gantt segment, mirroring graph.py's
 * with_phase_events stage labels (inject / verify / recovery). Nodes
 * not listed — intent_clarification, plan_builder, intent_confirm,
 * safety_check, confirmation_gate, terminal_reports (postmortem) —
 * land in "prep", as does any future unregistered node. */
const PHASE_OF: Record<string, PhaseKey> = {
  preplan_probe: "inject",
  batch_setup: "inject",
  agent_loop: "inject",
  baseline_capture: "inject",
  se_snapshot: "inject",
  execute_loop: "inject",
  verifier_loop: "verify",
  finalize_verification: "verify",
  se_detect: "verify",
  recover_verifier_loop: "recover",
  finalize_recover_verification: "recover",
};

function phaseOf(node: string): PhaseKey {
  return PHASE_OF[node] ?? "prep";
}

/** "task-1a2b3c4d…" → "1a2b3c4d" for the gantt's Y axis. */
function shortId(id: string): string {
  return id.replace(/^task-/, "").slice(0, 8) || id;
}

/** Exported for direct unit tests: the aggregation IS the page's
 * core logic, and DOM assertions (tick presence) cannot catch a
 * misclassified phase — the bars render either way. */
export async function fetchCharts(client: BladeClient): Promise<ChartsData> {
  const list = await client.listTasks();
  const tasks = list?.["tasks"];
  const ids = (Array.isArray(tasks) ? tasks : [])
    .slice(0, CHART_TASKS)
    .map((t) =>
      t && typeof t === "object" ? String(t["task_id"] ?? "") : "",
    )
    .filter(Boolean);

  const settled = await Promise.allSettled(
    ids.map((id) => client.getTaskMetric(id)),
  );

  const gantt: GanttRow[] = [];
  const byModel = new Map<string, UsageRow>();
  for (const r of settled) {
    if (r.status !== "fulfilled") continue;
    const d = r.value;
    if (!d || typeof d !== "object") continue;
    const id = String(d["task_id"] ?? "");
    if (!id) continue;

    const row: GanttRow = { id: shortId(id), prep: 0, inject: 0, verify: 0, recover: 0 };
    const spans = d["spans"];
    if (Array.isArray(spans)) {
      for (const s of spans) {
        if (!s || typeof s !== "object") continue;
        const dur = Number(s["duration_ms"] ?? 0);
        if (!Number.isFinite(dur) || dur <= 0) continue;
        row[phaseOf(String(s["node_name"] ?? ""))] += dur;
      }
    }
    gantt.push(row);

    const summary = d["summary"];
    const s: Record<string, unknown> =
      summary && typeof summary === "object"
        ? (summary as Record<string, unknown>)
        : {};
    const key = String(d["model_name"] ?? "") || "__unknown__";
    const prev =
      byModel.get(key) ?? { model: "", input: 0, output: 0 };
    prev.input += Number(s["total_token_input"] ?? 0) || 0;
    prev.output += Number(s["total_token_output"] ?? 0) || 0;
    byModel.set(key, prev);
  }

  const usage = [...byModel.entries()]
    .map(([key, v]) => ({
      model: key === "__unknown__" ? ui().chartsModelUnknown : key,
      input: v.input,
      output: v.output,
    }))
    .sort((a, b) => b.input + b.output - (a.input + a.output));

  return { gantt, usage };
}

/** Forge legend: small colour dot + label text, horizontal wrap. */
function DotLegend({
  items,
}: {
  items: { key: string; color: string; label: string }[];
}) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1">
      {items.map((it) => (
        <span
          key={it.key}
          className="flex items-center gap-1.5 text-xs text-forge-text-secondary"
        >
          <span
            className="size-2 shrink-0 rounded-full"
            style={{ background: it.color }}
          />
          {it.label}
        </span>
      ))}
    </div>
  );
}

const tooltipStyle = {
  background: "var(--color-forge-card)",
  border: "1px solid var(--color-forge-border)",
  borderRadius: 8,
  fontSize: 12,
} as const;

const axisTick = {
  fill: "var(--color-forge-text-faint)",
  fontSize: 11,
} as const;

const categoryTick = {
  fill: "var(--color-forge-text-secondary)",
  fontSize: 11,
  fontFamily: "var(--font-mono)",
} as const;

function formatTokensAxis(v: number): string {
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v);
}

export function ChartsPage() {
  const { client } = useBoot();
  const query = useQuery({
    queryKey: ["charts"],
    queryFn: () => fetchCharts(client),
  });

  if (query.isPending) {
    return (
      <p className="py-12 text-center text-sm text-forge-text-faint">
        {ui().chartsLoading}
      </p>
    );
  }
  if (query.isError) {
    return (
      <div className="py-12 text-center">
        <p className="text-sm font-medium text-danger">
          {ui().chartsLoadFailed}
        </p>
        <p className="mt-1 font-mono text-xs text-forge-text-faint">
          {query.error instanceof Error
            ? query.error.message
            : String(query.error)}
        </p>
      </div>
    );
  }
  const { gantt, usage } = query.data ?? { gantt: [], usage: [] };
  if (gantt.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-forge-text-faint">
        {ui().chartsEmpty}
      </p>
    );
  }

  const ganttHeight = Math.max(gantt.length * ROW_HEIGHT + AXIS_EXTRA, 120);
  const usageHeight = Math.max(usage.length * ROW_HEIGHT + AXIS_EXTRA, 120);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-4xl px-6 py-6">
        <h1 className="text-lg font-medium">{ui().chartsTitle}</h1>

        <section className="mt-5">
          <h2 className="text-sm font-medium">{ui().chartsTimeline}</h2>
          <p className="mt-0.5 text-xs text-forge-text-faint">
            {ui().chartsTimelineHint}
          </p>
          <div className="mt-2">
            <DotLegend
              items={[
                { key: "prep", color: "var(--color-forge-text-faint)", label: ui().chartsPhasePrep },
                { key: "inject", color: "var(--color-forge-accent)", label: ui().chartsPhaseInject },
                { key: "verify", color: "var(--color-forge-text-secondary)", label: ui().chartsPhaseVerify },
                { key: "recover", color: "var(--color-success-dot)", label: ui().chartsPhaseRecover },
              ]}
            />
          </div>
          <div className="mt-2 overflow-x-auto">
            <BarChart
              width={CHART_WIDTH}
              height={ganttHeight}
              data={gantt}
              layout="vertical"
              margin={{ top: 4, right: 16, bottom: 4, left: 4 }}
            >
              <CartesianGrid
                horizontal={false}
                stroke="var(--color-forge-border)"
              />
              <XAxis
                type="number"
                tick={axisTick}
                stroke="var(--color-forge-border)"
                tickFormatter={(v) => formatDuration(Number(v))}
              />
              <YAxis
                type="category"
                dataKey="id"
                width={120}
                reversed
                tick={categoryTick}
                stroke="var(--color-forge-border)"
              />
              <Tooltip
                formatter={(v) => formatDuration(Number(v))}
                contentStyle={tooltipStyle}
                labelStyle={{ color: "var(--color-forge-text)" }}
                cursor={{ fill: "var(--color-forge-sidebar)" }}
              />
              <Bar dataKey="prep" stackId="p" isAnimationActive={false} fill="var(--color-forge-text-faint)" name={ui().chartsPhasePrep} />
              <Bar dataKey="inject" stackId="p" isAnimationActive={false} fill="var(--color-forge-accent)" name={ui().chartsPhaseInject} />
              <Bar dataKey="verify" stackId="p" isAnimationActive={false} fill="var(--color-forge-text-secondary)" name={ui().chartsPhaseVerify} />
              <Bar dataKey="recover" stackId="p" isAnimationActive={false} fill="var(--color-success-dot)" name={ui().chartsPhaseRecover} />
            </BarChart>
          </div>
        </section>

        <section className="mt-8">
          <h2 className="text-sm font-medium">{ui().chartsUsage}</h2>
          <p className="mt-0.5 text-xs text-forge-text-faint">
            {ui().chartsUsageHint}
          </p>
          <div className="mt-2">
            <DotLegend
              items={[
                { key: "in", color: "var(--color-forge-text-secondary)", label: ui().chartsTokensIn },
                { key: "out", color: "var(--color-forge-accent)", label: ui().chartsTokensOut },
              ]}
            />
          </div>
          <div className="mt-2 overflow-x-auto">
            <BarChart
              width={CHART_WIDTH}
              height={usageHeight}
              data={usage}
              layout="vertical"
              margin={{ top: 4, right: 16, bottom: 4, left: 4 }}
            >
              <CartesianGrid
                horizontal={false}
                stroke="var(--color-forge-border)"
              />
              <XAxis
                type="number"
                tick={axisTick}
                stroke="var(--color-forge-border)"
                tickFormatter={formatTokensAxis}
              />
              <YAxis
                type="category"
                dataKey="model"
                width={190}
                reversed
                tick={categoryTick}
                stroke="var(--color-forge-border)"
                tickFormatter={(m: string) =>
                  m.length > 26 ? `${m.slice(0, 25)}…` : m
                }
              />
              <Tooltip
                formatter={(v) => Number(v).toLocaleString("en-US")}
                contentStyle={tooltipStyle}
                labelStyle={{ color: "var(--color-forge-text)" }}
                cursor={{ fill: "var(--color-forge-sidebar)" }}
              />
              <Bar dataKey="input" stackId="t" isAnimationActive={false} fill="var(--color-forge-text-secondary)" name={ui().chartsTokensIn} />
              <Bar dataKey="output" stackId="t" isAnimationActive={false} fill="var(--color-forge-accent)" name={ui().chartsTokensOut} />
            </BarChart>
          </div>
        </section>
      </div>
    </div>
  );
}

export default ChartsPage;
