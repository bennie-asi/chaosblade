/**
 * DAG panel model (design §7.2) — the LangGraph execution graph as a
 * fixed vertical flow, hand-laid-out (no @xyflow/react: the panel is a
 * 380px sidebar with a FIXED 17-node graph; drag/zoom buy nothing and
 * the dependency would bloat the first-paint chunk, violating §7.4).
 *
 * The node set mirrors graph.py's ``with_phase_events`` registrations
 * EXHAUSTIVELY — those 17 are the only nodes that emit
 * node_start/node_end and can therefore light up. Screener / ToolNode
 * plumbing never emits; its round-trip semantics collapse into the
 * self-loop badge on the four loop nodes (agent_loop, execute_loop,
 * verifier_loop, recover_verifier_loop). A node added to graph.py
 * without landing here is caught by the exhaustive pin in
 * dagModel.test.ts.
 *
 * Layout is data-driven: groups render in order, nodes stack inside
 * their phase group, y accumulates — inserting a node reflows instead
 * of corrupting hand-computed coordinates. Edges: main-chain adjacency
 * (automatic), self-loop badges (``loop: true``), and one handwritten
 * replan back-edge (finalize_verification ⤏ agent_loop). The recovery
 * group is deliberately disconnected — a recover session runs its own
 * graph (recover_verifier_loop ⇄ tools → finalize), not a branch of
 * the main pipeline.
 *
 * Node names render VERBATIM (they are the event keys); captions carry
 * the business semantics via uiText. Phase group titles reuse core's
 * ``t("phase.*")`` dictionary so this panel can never drift from the
 * PhaseStepper wording; postmortem (ignored by the stepper) gets a
 * web-local key.
 */
import { t } from "@blade-ai/core";
import { ui } from "./uiText";

export type DagPhase =
  | "intent"
  | "safety"
  | "inject"
  | "verify"
  | "recovery"
  | "postmortem";

/** Caption keys live in web's uiText (added alongside this model). */
export type DagCaptionKey =
  | "dagCapIntentClarification"
  | "dagCapPlanBuilder"
  | "dagCapIntentConfirm"
  | "dagCapSafetyCheck"
  | "dagCapConfirmationGate"
  | "dagCapPreplanProbe"
  | "dagCapBatchSetup"
  | "dagCapAgentLoop"
  | "dagCapBaselineCapture"
  | "dagCapSeSnapshot"
  | "dagCapExecuteLoop"
  | "dagCapVerifierLoop"
  | "dagCapFinalizeVerification"
  | "dagCapSeDetect"
  | "dagCapTerminalReports"
  | "dagCapRecoverVerifierLoop"
  | "dagCapFinalizeRecoverVerification";

export interface DagNodeDef {
  /** graph.py node name — the node_start/node_end event key. */
  id: string;
  phase: DagPhase;
  captionKey: DagCaptionKey;
  /** Round-trips through its screener/tools sub-loop. */
  loop?: boolean;
}

/** Registration order = execution order on the happy path. */
export const DAG_NODES: readonly DagNodeDef[] = [
  // — dialogue graph: clarification → intent confirmation.
  { id: "intent_clarification", phase: "intent", captionKey: "dagCapIntentClarification" },
  { id: "plan_builder", phase: "intent", captionKey: "dagCapPlanBuilder" },
  // — safety: intent confirm card, whitelist screen, human gate.
  { id: "intent_confirm", phase: "safety", captionKey: "dagCapIntentConfirm" },
  { id: "safety_check", phase: "safety", captionKey: "dagCapSafetyCheck" },
  { id: "confirmation_gate", phase: "safety", captionKey: "dagCapConfirmationGate" },
  // — main pipeline: probe → (batch) → agent loop → baseline → execute.
  { id: "preplan_probe", phase: "inject", captionKey: "dagCapPreplanProbe" },
  { id: "batch_setup", phase: "inject", captionKey: "dagCapBatchSetup" },
  { id: "agent_loop", phase: "inject", captionKey: "dagCapAgentLoop", loop: true },
  { id: "baseline_capture", phase: "inject", captionKey: "dagCapBaselineCapture" },
  { id: "se_snapshot", phase: "inject", captionKey: "dagCapSeSnapshot" },
  { id: "execute_loop", phase: "inject", captionKey: "dagCapExecuteLoop", loop: true },
  // — verification: verifier loop → finalize → side-effect diff.
  { id: "verifier_loop", phase: "verify", captionKey: "dagCapVerifierLoop", loop: true },
  { id: "finalize_verification", phase: "verify", captionKey: "dagCapFinalizeVerification" },
  { id: "se_detect", phase: "verify", captionKey: "dagCapSeDetect" },
  // — terminal artifacts (the stepper ignores this phase; the DAG doesn't).
  { id: "terminal_reports", phase: "postmortem", captionKey: "dagCapTerminalReports" },
  // — recovery graph (disconnected: its own session type).
  { id: "recover_verifier_loop", phase: "recovery", captionKey: "dagCapRecoverVerifierLoop", loop: true },
  { id: "finalize_recover_verification", phase: "recovery", captionKey: "dagCapFinalizeRecoverVerification" },
];

/** verify-replan: finalize_verification loops back to agent_loop. */
export const REPLAN_EDGE: readonly [from: string, to: string] = [
  "finalize_verification",
  "agent_loop",
];

// ---------------------------------------------------------------------------
// Layout — accumulated, not hand-computed. The panel scrolls if the
// flow outgrows the viewport (17 nodes ≈ 1000px tall).
// ---------------------------------------------------------------------------

export const DAG_WIDTH = 340;
/** Wide enough for the longest node id (finalize_recover_verification,
 *  29 mono glyphs ≈ 180px) next to the status dot without truncation —
 *  node names render VERBATIM, eliding the event key is not an option. */
export const NODE_W = 216;
export const NODE_H = 44;
const NODE_GAP = 14;
const GROUP_LABEL_H = 26;
const GROUP_GAP = 10;
/** Main-chain x; the left gutter hosts the replan back-edge channel. */
const NODE_X = 84;
/** Vertical channel the replan back-edge routes through — kept clear
 *  of the node column (x=84) and of group labels (node-aligned). */
export const BACK_EDGE_X = 44;

export interface DagNodeLayout extends DagNodeDef {
  x: number;
  y: number;
}

export interface DagEdge {
  /** Main-chain segment between two adjacent laid-out nodes. The id
   *  pair lets the renderer colour an edge by endpoint state without
   *  reverse-engineering coordinates. */
  from: string;
  to: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface DagLayout {
  nodes: DagNodeLayout[];
  /** Main-chain adjacency edges; group boundaries are EXCLUDED — the
   *  group label carries the transition (see layoutDag). */
  edges: DagEdge[];
  /** Group labels with their y anchor. */
  groups: { phase: DagPhase; label: string; y: number }[];
  height: number;
}

/** Phase-group title: core's shared phase.* dictionary for the five
 *  stepper phases (wording parity with PhaseStepper is the point);
 *  web-local for postmortem, which the stepper deliberately ignores. */
export function dagGroupLabel(phase: DagPhase): string {
  return phase === "postmortem" ? ui().dagGroupPostmortem : t(`phase.${phase}`);
}

export function layoutDag(): DagLayout {
  const nodes: DagNodeLayout[] = [];
  const edges: DagEdge[] = [];
  const groups: DagLayout["groups"] = [];
  let y = 8;
  let prevPhase: DagPhase | null = null;
  let prev: { id: string; bottomX: number; bottomY: number } | null = null;

  for (const def of DAG_NODES) {
    if (def.phase !== prevPhase) {
      if (prevPhase !== null) y += GROUP_GAP;
      groups.push({ phase: def.phase, label: dagGroupLabel(def.phase), y });
      y += GROUP_LABEL_H;
      prevPhase = def.phase;
      // A group boundary breaks the visual chain — no edge drawn into
      // the first node of a new group (the label carries the transition).
      prev = null;
    }
    nodes.push({ ...def, x: NODE_X, y });
    const topCenter = { x: NODE_X + NODE_W / 2, y };
    if (prev) {
      edges.push({
        from: prev.id,
        to: def.id,
        x1: prev.bottomX,
        y1: prev.bottomY,
        x2: topCenter.x,
        y2: topCenter.y,
      });
    }
    prev = { id: def.id, bottomX: topCenter.x, bottomY: y + NODE_H };
    y += NODE_H + NODE_GAP;
  }

  return { nodes, edges, groups, height: y };
}
