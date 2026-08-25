/**
 * dagModel pins.
 *
 * The load-bearing test is the EXHAUSTIVE pin: DAG_NODES must equal
 * graph.py's ``with_phase_events`` registrations — those 17 are the
 * only nodes that emit node_start/node_end and can therefore light
 * up. A node added to graph.py without landing here fails the set
 * equality below instead of silently never lighting on the panel
 * (same rationale as ChartsPage's PHASE_OF pin: the panel renders
 * fine either way, so only an explicit set comparison can see it).
 */
import { configureI18n, t } from "@blade-ai/core";
import { describe, expect, it } from "vitest";
import { ui } from "./uiText";
import {
  DAG_NODES,
  DAG_WIDTH,
  NODE_H,
  NODE_W,
  REPLAN_EDGE,
  dagGroupLabel,
  layoutDag,
} from "./dagModel";

// Vitest runs under Node where the i18n default sniffs
// BLADE_AI_LANG — pin a known starting language like the other
// web suites do.
configureI18n("en");

/** graph.py's with_phase_events registrations, verbatim: [node, phase]. */
const GRAPH_PY_NODES = [
  ["intent_clarification", "intent"],
  ["plan_builder", "intent"],
  ["intent_confirm", "safety"],
  ["safety_check", "safety"],
  ["confirmation_gate", "safety"],
  ["preplan_probe", "inject"],
  ["batch_setup", "inject"],
  ["agent_loop", "inject"],
  ["baseline_capture", "inject"],
  ["se_snapshot", "inject"],
  ["execute_loop", "inject"],
  ["verifier_loop", "verify"],
  ["finalize_verification", "verify"],
  ["se_detect", "verify"],
  ["terminal_reports", "postmortem"],
  ["recover_verifier_loop", "recovery"],
  ["finalize_recover_verification", "recovery"],
] as const;

describe("dagModel / node set", () => {
  it("mirrors graph.py's with_phase_events registrations EXHAUSTIVELY", () => {
    const ids = DAG_NODES.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length); // no duplicates
    expect([...ids].sort()).toEqual(GRAPH_PY_NODES.map(([id]) => id).sort());
  });

  it("pins each node's phase attribution to graph.py's registration", () => {
    // Set equality alone can't see a node filed under the WRONG group —
    // same ids, same group count, green tests, mislabeled panel.
    const phaseById = new Map(DAG_NODES.map((n) => [n.id, n.phase]));
    for (const [id, phase] of GRAPH_PY_NODES) {
      expect(phaseById.get(id)).toBe(phase);
    }
  });

  it("flags exactly the four screener/tools round-trip nodes as loops", () => {
    expect(DAG_NODES.filter((n) => n.loop).map((n) => n.id).sort()).toEqual([
      "agent_loop",
      "execute_loop",
      "recover_verifier_loop",
      "verifier_loop",
    ]);
  });

  it("replan back-edge points from finalize_verification to agent_loop", () => {
    const [from, to] = REPLAN_EDGE;
    const fromIdx = DAG_NODES.findIndex((n) => n.id === from);
    const toIdx = DAG_NODES.findIndex((n) => n.id === to);
    expect(fromIdx).toBeGreaterThanOrEqual(0);
    expect(toIdx).toBeGreaterThanOrEqual(0);
    // A back-edge must point UP the flow — from later to earlier.
    expect(fromIdx).toBeGreaterThan(toIdx);
  });
});

describe("dagModel / captions & group labels", () => {
  it("resolves every caption key in both locales", () => {
    for (const lang of ["en", "zh"] as const) {
      configureI18n(lang);
      for (const node of DAG_NODES) {
        const caption = ui()[node.captionKey];
        expect(typeof caption).toBe("string");
        expect(caption.length).toBeGreaterThan(0);
      }
      // Panel chrome keys the component will need.
      expect(ui().dagPanelTitle.length).toBeGreaterThan(0);
      expect(ui().dagEmptyHint.length).toBeGreaterThan(0);
      expect(ui().dagLoopBadge.length).toBeGreaterThan(0);
      expect(ui().dagReplanLabel.length).toBeGreaterThan(0);
    }
    configureI18n("en");
  });

  it("reuses core's phase.* dictionary for the five stepper phases", () => {
    for (const phase of ["intent", "safety", "inject", "verify", "recovery"] as const) {
      expect(dagGroupLabel(phase)).toBe(t(`phase.${phase}`));
    }
  });

  it("gives postmortem a web-local label (the stepper ignores that phase)", () => {
    expect(dagGroupLabel("postmortem")).toBe(ui().dagGroupPostmortem);
  });
});

describe("dagModel / layout", () => {
  it("keeps registration order, stacks downward without overlap", () => {
    const { nodes } = layoutDag();
    expect(nodes.map((n) => n.id)).toEqual(DAG_NODES.map((n) => n.id));
    for (let i = 1; i < nodes.length; i++) {
      // Strictly below the previous node's bottom edge.
      expect(nodes[i].y).toBeGreaterThanOrEqual(nodes[i - 1].y + NODE_H);
    }
  });

  it("stays inside the panel width", () => {
    const { nodes } = layoutDag();
    for (const n of nodes) {
      expect(n.x).toBeGreaterThanOrEqual(0);
      expect(n.x + NODE_W).toBeLessThanOrEqual(DAG_WIDTH);
    }
  });

  it("draws main-chain edges only inside a group (labels carry transitions)", () => {
    const { nodes, edges, groups } = layoutDag();
    // 17 nodes, 6 phase groups → one edge per same-group adjacency.
    expect(edges).toHaveLength(nodes.length - groups.length);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    for (const e of edges) {
      // Vertical connector: same center-x, top below bottom.
      expect(e.x1).toBe(e.x2);
      expect(e.y2).toBeGreaterThan(e.y1);
      // Endpoints land on real node boundaries, and the id pair
      // matches the coordinates (the renderer colours by id, so an
      // id/coordinate mismatch would mislight an edge silently).
      const from = byId.get(e.from);
      const to = byId.get(e.to);
      expect(from).toBeDefined();
      expect(to).toBeDefined();
      expect(e.x1).toBe(from!.x + NODE_W / 2);
      expect(e.y1).toBe(from!.y + NODE_H);
      expect(e.x2).toBe(to!.x + NODE_W / 2);
      expect(e.y2).toBe(to!.y);
    }
    // Groups appear in first-appearance order and anchor above their nodes.
    expect(groups.map((g) => g.phase)).toEqual([
      "intent",
      "safety",
      "inject",
      "verify",
      "postmortem",
      "recovery",
    ]);
    for (const g of groups) {
      const first = nodes.find((n) => n.phase === g.phase);
      expect(first).toBeDefined();
      expect(g.y).toBeLessThan(first!.y);
    }
    expect(byId.size).toBe(nodes.length);
  });

  it("covers the last node's bottom edge with the reported height", () => {
    const { nodes, height } = layoutDag();
    const lastBottom = Math.max(...nodes.map((n) => n.y + NODE_H));
    expect(height).toBeGreaterThanOrEqual(lastBottom);
  });
});
