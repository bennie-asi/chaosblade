/**
 * ProgressRail — the right progress rail (design §7.2 + mock v2
 * column 4). Fixed panel order: summary → countdown → phases →
 * injection params → safety checks → progress timeline, with the
 * execution-graph entry (full-screen DagOverlay) at the bottom.
 *
 * The rail renders ONLY while a drill is live (useActiveDrill) — no
 * task, no rail. Collapse persists via railStore; the <1250px
 * viewport gate lives in ChatPage's visibility rule (distributed to
 * the chat column through ProgressRailContext), so this component
 * only decides between the full column and the slim re-open strip.
 *
 * Data honesty rules (tasks.md 2.7 deviations):
 *   - countdown anchors on the earliest ``execute_loop`` progress
 *     line's wall-clock ts (span start_time is a process monotonic
 *     clock and cannot anchor wall time); no anchor / no duration /
 *     past deadline → the box does not render;
 *   - the safety card renders the metric's real fields
 *     (safety_status / safety_reason / feasibility_report), not the
 *     mock's illustrative whitelist/conflict/capacity rows;
 *   - the phase panel falls back from the live store stepper to the
 *     most recent committed PhaseStepperItem after the turn commits.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Check,
  Copy,
  Network,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";
import { formatFaultType, useAppSelector, type BladeClient } from "@blade-ai/core";
import { ui } from "../../lib/uiText";
import { asString } from "../../lib/utils";
import { latestTurnProgressItems } from "../../lib/nodeProgress";
import { TimelinePanel } from "../timeline/TimelinePanel";
import { PhaseStepperStrip } from "../chat/PhaseStepper";
import { DagOverlay } from "../dag/DagOverlay";
import { useRailCollapsed } from "./railStore";
import { useActiveDrill } from "./useActiveDrill";

type Metric = Record<string, unknown>;

function specOf(metric: Metric | undefined): Metric {
  return ((metric?.["fault_spec"] ?? {}) as Metric) ?? {};
}

/** "2026-08-21 14:32:07" / ISO → "14:32:07" — slice-based (server
 *  stamps are already local wall time; Date-parsing them is both
 *  lossy and Safari-fragile). */
function timePart(raw: string): string {
  const norm = raw.replace("T", " ");
  return norm.length >= 19 ? norm.slice(11, 19) : "";
}

function formatCountdown(totalSec: number): string {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** status → small dot. Forge discipline: a dot + text, never a pill. */
function statusDotClass(status: string): string {
  switch (status) {
    case "passed":
    case "ok":
      return "bg-success-dot";
    case "rejected":
    case "impossible":
      return "bg-danger";
    case "tight":
      return "bg-warning-dot";
    default:
      return "bg-forge-text-faint";
  }
}

/** Assemble the raw blade command from the fault spec — the audit
 *  and reproduction credential shown under the params card. */
export function buildBladeCommand(spec: Metric): string {
  const parts = ["blade", "create", "k8s"];
  const st = [asString(spec["scope"]), asString(spec["fault_target"])]
    .filter(Boolean)
    .join("-");
  if (st) parts.push(st);
  const action = asString(spec["fault_action"]);
  if (action) parts.push(action);
  const params = spec["params"];
  if (params && typeof params === "object") {
    for (const [k, v] of Object.entries(params as Metric)) {
      parts.push(`--${k}`, String(v));
    }
  }
  const flags = Array.isArray(spec["params_flags"]) ? spec["params_flags"] : [];
  for (const f of flags) parts.push(`--${String(f)}`);
  const ns = asString(spec["namespace"]);
  if (ns) parts.push("--namespace", ns);
  const labels = spec["labels"];
  if (labels && typeof labels === "object") {
    const joined = Object.entries(labels as Metric)
      .map(([k, v]) => `${k}=${v}`)
      .join(",");
    if (joined) parts.push("--labels", joined);
  }
  const duration = Number(spec["duration_seconds"] ?? 0);
  if (duration > 0) parts.push("--timeout", String(duration));
  return parts.join(" ");
}

/** Epoch ms → "HH:MM:SS" local wall clock (for the approved ts). */
function fmtClock(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function SummaryCard({
  metric,
  approvedTs,
}: {
  metric: Metric | undefined;
  approvedTs?: number;
}) {
  const spec = specOf(metric);
  const faultLine =
    [asString(spec["fault_target"]), asString(spec["fault_action"])]
      .filter(Boolean)
      .join(" ") || (metric ? formatFaultType(metric) : "");
  const ns = asString(spec["namespace"]);
  const labels = spec["labels"];
  const labelStr =
    labels && typeof labels === "object"
      ? Object.entries(labels as Metric)
          .map(([k, v]) => `${k}=${v}`)
          .join(",")
      : "";
  const names = Array.isArray(spec["names"]) ? spec["names"] : [];
  const scope = asString(spec["scope"]);
  const targetLine = [
    labelStr || names.slice(0, 3).map(String).join(", "),
    ns,
    names.length > 1 ? `× ${names.length} ${scope}`.trim() : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const uid = asString(metric?.["experiment_uid"]);
  const at = timePart(asString(metric?.["gmt_create"]));
  // Mock row: ``uid … · 14:32:07 批准注入``. The approved moment is
  // the earliest execute_loop progress ts (confirmation_gate passed
  // ⇒ execute_loop started) — the first wall-clock evidence that the
  // gate approved. Without it the row falls back to gmt_create with
  // NO suffix (a creation time is not an approval).
  const approved = approvedTs !== undefined ? fmtClock(approvedTs) : "";

  return (
    <section className="border-b border-forge-border px-3 py-2.5">
      <p className="flex items-center gap-2 text-sm font-medium">
        <span className="inline-block size-2 shrink-0 rounded-full bg-warning-dot" />
        <span className="font-mono">{faultLine || "—"}</span>
      </p>
      {targetLine && (
        <p className="mt-1 pl-4 text-xs text-forge-text-secondary">{targetLine}</p>
      )}
      {(uid || approved || at) && (
        <p className="mt-1 pl-4 font-mono text-[11px] text-forge-text-faint">
          {uid && `uid ${uid.slice(0, 12)}`}
          {uid && (approved || at) && " · "}
          {approved ? `${approved} ${ui().railApproved}` : at}
        </p>
      )}
    </section>
  );
}

function CountdownBox({
  metric,
  anchorTs,
}: {
  metric: Metric | undefined;
  anchorTs: number | undefined;
}) {
  const durationSec = Number(specOf(metric)["duration_seconds"] ?? 0);
  const taskState = asString(metric?.["task_state"]);
  const [now, setNow] = useState(() => Date.now());

  const active =
    (taskState === "injecting" || taskState === "injected") &&
    durationSec > 0 &&
    anchorTs !== undefined;
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);

  if (!active) return null;
  const remaining = durationSec - Math.floor((now - anchorTs) / 1000);
  if (remaining <= 0) return null;
  return (
    <section className="flex items-center gap-2 border-b border-forge-border px-3 py-2.5">
      <span className="text-xs text-forge-text-secondary">
        {ui().railCountdown}
      </span>
      <span className="ml-auto font-mono text-sm font-medium tabular-nums">
        {formatCountdown(remaining)}
      </span>
    </section>
  );
}

function PhasePanel() {
  const live = useAppSelector((s) => s.currentPhaseStepper);
  const history = useAppSelector((s) => s.history);
  const committed = useMemo(() => {
    for (let i = history.length - 1; i >= 0; i--) {
      const item = history[i];
      if (item.kind === "phase_stepper") return item;
    }
    return null;
  }, [history]);
  const steps = live?.steps ?? committed?.steps;
  if (!steps) return null;
  return (
    <section className="border-b border-forge-border px-3 py-2.5">
      <p className="mb-2 text-xs font-medium text-forge-text-secondary">
        {ui().railPhaseTitle}
      </p>
      <PhaseStepperStrip steps={steps} live={live !== null} />
    </section>
  );
}

function ParamsCard({ metric }: { metric: Metric | undefined }) {
  const spec = specOf(metric);
  const [copied, setCopied] = useState(false);
  if (!metric || Object.keys(spec).length === 0) return null;

  const scopeTarget = [asString(spec["scope"]), asString(spec["fault_target"])]
    .filter(Boolean)
    .join(" / ");
  const action = asString(spec["fault_action"]);
  const params = (spec["params"] ?? {}) as Metric;
  const duration = Number(spec["duration_seconds"] ?? 0);
  const ns = asString(spec["namespace"]);
  const labels = (spec["labels"] ?? {}) as Metric;
  const cmd = buildBladeCommand(spec);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cmd);
    } catch {
      // Clipboard API unavailable (insecure context / test env) — the
      // visual feedback still fires so the row doesn't feel dead.
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="border-b border-forge-border px-3 py-2.5">
      <p className="mb-1.5 text-xs font-medium text-forge-text-secondary">
        {ui().railParamsTitle}
      </p>
      <dl className="flex flex-col gap-1 text-xs">
        {scopeTarget && <Kv k="scope · target" v={scopeTarget} />}
        {action && <Kv k="action" v={action} />}
        {Object.entries(params).map(([k, v]) => (
          <Kv key={k} k={k} v={String(v)} />
        ))}
        {duration > 0 && (
          <Kv
            k="duration"
            v={`${duration}s (${ui().railDurationAuto})`}
          />
        )}
        {ns && <Kv k="namespace" v={ns} />}
        {Object.keys(labels).length > 0 && (
          <Kv
            k="labels"
            v={Object.entries(labels)
              .map(([k, v]) => `${k}=${v}`)
              .join(",")}
          />
        )}
      </dl>
      <div className="mt-2 flex items-start gap-1.5 rounded-button bg-forge-border/40 px-2 py-1.5">
        <code className="min-w-0 flex-1 whitespace-pre-wrap break-all font-mono text-[11px] text-forge-text-secondary">
          {cmd}
        </code>
        <button
          type="button"
          onClick={copy}
          title={copied ? ui().railCopied : ui().railCopy}
          aria-label={copied ? ui().railCopied : ui().railCopy}
          className="shrink-0 rounded-button p-1 text-forge-text-faint hover:text-forge-text-secondary"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
        </button>
      </div>
    </section>
  );
}

function Kv({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="w-24 shrink-0 text-forge-text-faint">{k}</dt>
      <dd className="min-w-0 flex-1 break-words font-mono text-forge-text-secondary">
        {v}
      </dd>
    </div>
  );
}

function SafetyCard({ metric }: { metric: Metric | undefined }) {
  const status = asString(metric?.["safety_status"]);
  const reason = asString(metric?.["safety_reason"]).trim();
  const feas = metric?.["feasibility_report"];
  const feasRec =
    feas && typeof feas === "object" ? (feas as Metric) : undefined;
  const feasSeverity = asString(feasRec?.["severity"]);
  const feasMessage = asString(feasRec?.["message"]).trim();
  if (!status && !feasRec) return null;
  return (
    <section className="border-b border-forge-border px-3 py-2.5">
      <p className="mb-1.5 text-xs font-medium text-forge-text-secondary">
        {ui().railSafetyTitle}
      </p>
      <div className="flex flex-col gap-1.5 text-xs">
        {status && (
          <p className="flex items-start gap-2">
            <span
              className={`mt-1 inline-block size-2 shrink-0 rounded-full ${statusDotClass(status)}`}
            />
            <span className="min-w-0 flex-1 text-forge-text-secondary">
              safety {status}
              {reason && (
                <span className="text-forge-text-faint"> — {reason}</span>
              )}
            </span>
          </p>
        )}
        {feasRec && (
          <p className="flex items-start gap-2">
            <span
              className={`mt-1 inline-block size-2 shrink-0 rounded-full ${statusDotClass(feasSeverity)}`}
            />
            <span className="min-w-0 flex-1 text-forge-text-secondary">
              feasibility {feasSeverity || "?"}
              {feasMessage && (
                <span className="text-forge-text-faint"> — {feasMessage}</span>
              )}
            </span>
          </p>
        )}
      </div>
    </section>
  );
}

export function ProgressRail({ client }: { client: BladeClient }) {
  const { metric, isLive } = useActiveDrill(client);
  const [collapsed, toggle] = useRailCollapsed();
  const [dagOpen, setDagOpen] = useState(false);
  const history = useAppSelector((s) => s.history);
  // Countdown anchor: the earliest execute_loop progress line's
  // wall-clock ts (see module docstring for why spans can't serve),
  // scoped to the latest turn so an earlier drill never leaks in
  // (see lib/nodeProgress.ts's latestTurnProgressItems).
  const anchorTs = useMemo(
    () =>
      latestTurnProgressItems(history).find(
        (e) => e.tag === "execute_loop" && e.ts !== undefined,
      )?.ts,
    [history],
  );

  if (!isLive) return null;

  if (collapsed) {
    return (
      <aside className="w-8 shrink-0 border-l border-forge-border bg-forge-sidebar">
        <button
          type="button"
          onClick={toggle}
          title={ui().railExpand}
          aria-label={ui().railExpand}
          className="flex w-full items-start justify-center pt-3 text-forge-text-faint hover:text-forge-text-secondary"
        >
          <PanelRightOpen size={14} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="flex w-[380px] shrink-0 flex-col border-l border-forge-border bg-forge-sidebar">
      <header className="flex h-10 shrink-0 items-center gap-2 border-b border-forge-border px-3">
        <span className="text-xs font-medium text-forge-text-secondary">
          {ui().railTitle}
        </span>
        <span className="flex items-center gap-1 text-[11px] font-medium text-forge-accent">
          <span className="inline-block size-1.5 rounded-full bg-forge-accent animate-pulse motion-reduce:animate-none" />
          {ui().railLive}
        </span>
        <button
          type="button"
          onClick={toggle}
          title={ui().railCollapse}
          aria-label={ui().railCollapse}
          className="ml-auto rounded-button p-1 text-forge-text-faint hover:bg-forge-border/60 hover:text-forge-text-secondary"
        >
          <PanelRightClose size={14} />
        </button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <SummaryCard metric={metric} approvedTs={anchorTs} />
        <CountdownBox metric={metric} anchorTs={anchorTs} />
        <PhasePanel />
        <ParamsCard metric={metric} />
        <SafetyCard metric={metric} />
        <TimelinePanel />
        <button
          type="button"
          onClick={() => setDagOpen(true)}
          className="flex shrink-0 items-center gap-2 border-t border-forge-border px-3 py-2.5 text-left text-xs text-forge-text-secondary hover:bg-forge-border/40"
        >
          <Network size={14} className="shrink-0 text-forge-text-faint" />
          <span>{ui().railViewDag}</span>
          <span className="ml-auto text-[11px] text-forge-text-faint">
            {ui().railViewDagHint} →
          </span>
        </button>
      </div>
      <DagOverlay open={dagOpen} onClose={() => setDagOpen(false)} />
    </aside>
  );
}
