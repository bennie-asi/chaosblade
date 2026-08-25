/**
 * Phase stepper — the inject/recover pipeline progress strip.
 *
 * Two renderings of the same data (``PhaseStep[]`` from core):
 *
 *   - ``LivePhaseStepper`` reads ``state.currentPhaseStepper`` and is
 *     pinned by Composer directly above the input row while a turn is
 *     in flight. It renders NOTHING for chat / Q&A turns and on old
 *     servers that omit ``phase`` from node_start events — the
 *     reducer's lazy-materialisation gate keeps the slot null there,
 *     so plain dialogue never grows pipeline chrome. The live strip
 *     is the FALLBACK phase signal: it yields whenever the right
 *     progress rail is on screen (ProgressRailContext) and surfaces
 *     the moment the rail goes away — collapsed, narrow viewport, or
 *     no live drill. At most one live phase signal is on screen at a
 *     time (§7.2 零重复, dynamic-layout form).
 *   - The history variant renders from a ``PhaseStepperItem`` that
 *     ``commitPending`` appends at turn end — the finalised
 *     "where did this turn get to" summary in scrollback. Kept
 *     unconditionally: the DAG panel is live-state-only and has no
 *     historical presence, so this strip is the scrollback's sole
 *     per-turn phase trail.
 *
 * Forge discipline: status is a small dot + text label (never a
 * filled pill); forge-orange marks ONLY the in-progress step (the
 * design token's sanctioned "current state" use); the active dot
 * pulses gently while live so the strip reads as alive without a
 * spinner.
 */
import type { PhaseStatus, PhaseStep } from "@blade-ai/core";
import { t, useAppSelector } from "@blade-ai/core";
import { useProgressRailVisible } from "../rail/ProgressRailContext";

const DOT_CLASS: Record<PhaseStatus, string> = {
  completed: "bg-success-dot",
  in_progress: "bg-forge-accent",
  failed: "bg-danger",
  pending: "bg-forge-border",
};

const LABEL_CLASS: Record<PhaseStatus, string> = {
  completed: "text-forge-text-secondary",
  in_progress: "font-medium text-forge-accent",
  failed: "text-danger",
  pending: "text-forge-text-faint",
};

export function PhaseStepperStrip({
  steps,
  live = false,
}: {
  steps: PhaseStep[];
  live?: boolean;
}) {
  return (
    <ol className="flex flex-wrap items-center gap-y-1">
      {steps.map((step, i) => (
        <li key={step.id} className="flex items-center">
          {i > 0 && (
            <span
              aria-hidden="true"
              className="mx-2 h-px w-5 bg-forge-border"
            />
          )}
          <span
            data-status={step.status}
            className={`inline-block size-2 shrink-0 rounded-full ${DOT_CLASS[step.status]}${
              live && step.status === "in_progress"
                ? " animate-pulse motion-reduce:animate-none"
                : ""
            }`}
          />
          <span className={`ml-1.5 text-xs ${LABEL_CLASS[step.status]}`}>
            {t(`phase.${step.id}`)}
          </span>
          {stepEta(step, live) && (
            <span className="ml-1 text-[11px] tabular-nums text-forge-text-faint">
              {stepEta(step, live)}
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

/** Per-step eta (mock v2 phase panel): a completed/failed step shows
 *  its observed duration when both timestamps exist; the live
 *  in-progress step shows "running". Steps the reducer never
 *  timestamped (seed-rounded completions, untouched pendings) render
 *  no eta — an unobserved duration would be fabricated. */
function stepEta(step: PhaseStep, live: boolean): string {
  if (step.status === "in_progress") return live ? t("phase.running") : "";
  if (step.startedAt === undefined || step.completedAt === undefined) {
    return "";
  }
  const sec = Math.max(0, Math.round((step.completedAt - step.startedAt) / 1000));
  if (sec < 60) return `${sec}s`;
  return `${Math.floor(sec / 60)}m${String(sec % 60).padStart(2, "0")}s`;
}

/** Live strip for the Composer — null-rendering when no inject /
 *  recover turn is in flight, and yielding to the right progress rail
 *  whenever the rail is on screen (ProgressRailContext). Since the
 *  rail only exists while a drill is live, a null rail brings the
 *  strip back at any viewport width. Width: the rail is hidden here,
 *  so the strip always takes the chat column's 980px measure. */
export function LivePhaseStepper() {
  const stepper = useAppSelector((s) => s.currentPhaseStepper);
  const railVisible = useProgressRailVisible();
  if (!stepper || railVisible) return null;
  return (
    <div className="mx-auto max-w-[980px] px-4 pt-2">
      <PhaseStepperStrip steps={stepper.steps} live />
    </div>
  );
}
