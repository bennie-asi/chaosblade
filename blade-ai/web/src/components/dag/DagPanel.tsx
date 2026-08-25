/**
 * DAG panel (design §7.2) — the LangGraph execution graph, live-lit in
 * the right rail. The agent's "brain activity" made visible: nodes are
 * the 17 with_phase_events registrations from graph.py (dagModel pins
 * the set exhaustively), names render VERBATIM because they are the
 * node_start/node_end event keys, and captions carry the business
 * semantics underneath.
 *
 * Three node states, driven by core's per-turn tracking (reset on
 * TURN_STARTED; ``activeNodes`` additionally clears at every turn
 * commit and on session swap, so a finished turn leaves an all-done
 * graph — never a stuck lit node — until the next turn lights it):
 *
 *   - ``idle``   — never entered this turn (neutral card, faint text)
 *   - ``active`` — in ``activeNodes``: forge-accent border + name +
 *     pulsing status dot (accent's sanctioned "current state" use)
 *   - ``done``   — in ``visitedNodes``: green status dot, text recedes
 *
 * Forge discipline: status is a small dot + colour shift, never a
 * filled pill; no gradients; the only motion is the active dot's
 * pulse (and it stands down under prefers-reduced-motion via
 * motion-reduce:animate-none).
 *
 * The panel renders inside the full-screen DagOverlay (mock v2:
 * 查看执行图 entry in the ProgressRail) — the graph is a drill-down,
 * not an always-on rail occupant.
 */
import { useAppSelector } from "@blade-ai/core";
import { ui } from "../../lib/uiText";
import {
  BACK_EDGE_X,
  DAG_WIDTH,
  NODE_H,
  NODE_W,
  REPLAN_EDGE,
  layoutDag,
  type DagNodeLayout,
} from "../../lib/dagModel";

type DagNodeState = "idle" | "active" | "done";

const DOT_FILL: Record<DagNodeState, string> = {
  idle: "fill-forge-border",
  done: "fill-success-dot",
  active: "fill-forge-accent",
};

const RECT_CLASS: Record<DagNodeState, string> = {
  idle: "fill-forge-card stroke-forge-border",
  done: "fill-forge-card stroke-forge-border",
  active: "fill-forge-accent-soft stroke-forge-accent",
};

const NAME_FILL: Record<DagNodeState, string> = {
  idle: "fill-forge-text-faint",
  done: "fill-forge-text-secondary",
  active: "fill-forge-accent",
};

/** Edge into a node about to run goes accent (flow); an edge whose
 *  endpoints both ran recedes a shade stronger than the never-run
 *  skeleton. Marker ids pair with the stroke classes in <defs>. */
const EDGE_CLASS: Record<DagNodeState, string> = {
  idle: "stroke-forge-border",
  done: "stroke-forge-text-faint",
  active: "stroke-forge-accent",
};
const EDGE_MARKER: Record<DagNodeState, string> = {
  idle: "dag-arrow-idle",
  done: "dag-arrow-done",
  active: "dag-arrow-active",
};

function DagNode({
  node,
  state,
}: {
  node: DagNodeLayout;
  state: DagNodeState;
}) {
  const midY = node.y + NODE_H / 2;
  return (
    <g data-node-id={node.id} data-node-state={state}>
      <rect
        x={node.x}
        y={node.y}
        width={NODE_W}
        height={NODE_H}
        rx={8}
        className={RECT_CLASS[state]}
      />
      <circle
        cx={node.x + 14}
        cy={midY}
        r={3}
        className={`${DOT_FILL[state]}${
          state === "active" ? " animate-pulse motion-reduce:animate-none" : ""
        }`}
      />
      <text
        x={node.x + 26}
        y={node.y + 19}
        className={`font-mono text-[11px] ${NAME_FILL[state]}`}
      >
        {node.id}
      </text>
      <text
        x={node.x + 26}
        y={node.y + 33}
        className="fill-forge-text-faint text-[10px]"
      >
        {ui()[node.captionKey]}
      </text>
      {node.loop && (
        <text
          x={node.x + NODE_W - 12}
          y={node.y + 16}
          textAnchor="middle"
          className="fill-forge-text-faint text-[11px]"
        >
          <title>{ui().dagLoopBadge}</title>↻
        </text>
      )}
    </g>
  );
}

function DagGraph({
  activeNodes,
  visitedNodes,
}: {
  activeNodes: string[];
  visitedNodes: string[];
}) {
  // layoutDag resolves group labels through t()/ui() — recompute per
  // render so a language switch re-labels without a remount (17 nodes;
  // the arithmetic is trivial).
  const { nodes, edges, groups, height } = layoutDag();

  const stateOf = (id: string): DagNodeState =>
    activeNodes.includes(id)
      ? "active"
      : visitedNodes.includes(id)
        ? "done"
        : "idle";
  const byId = new Map(nodes.map((n) => [n.id, n]));

  const [replanFrom, replanTo] = REPLAN_EDGE;
  const fromNode = byId.get(replanFrom);
  const toNode = byId.get(replanTo);

  return (
    <svg
      role="img"
      aria-label={ui().dagPanelTitle}
      viewBox={`0 0 ${DAG_WIDTH} ${height}`}
      width="100%"
      className="block"
    >
      <defs>
        {(
          [
            ["dag-arrow-idle", "fill-forge-border"],
            ["dag-arrow-done", "fill-forge-text-faint"],
            ["dag-arrow-active", "fill-forge-accent"],
          ] as const
        ).map(([id, fill]) => (
          <marker
            key={id}
            id={id}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0.5 L 8 4 L 0 7.5 z" className={fill} />
          </marker>
        ))}
      </defs>

      {/* Main-chain edges, underneath the nodes. Colour by flow: an
          edge into the running node goes accent; an edge whose two
          endpoints both ran recedes a shade stronger than the
          never-run skeleton. */}
      {edges.map((e) => {
        const effective: DagNodeState =
          stateOf(e.to) === "active"
            ? "active"
            : stateOf(e.from) !== "idle" && stateOf(e.to) !== "idle"
              ? "done"
              : "idle";
        return (
          <line
            key={`${e.from}->${e.to}`}
            x1={e.x1}
            y1={e.y1}
            x2={e.x2}
            y2={e.y2 - 2}
            className={EDGE_CLASS[effective]}
            markerEnd={`url(#${EDGE_MARKER[effective]})`}
          />
        );
      })}

      {/* Verify-replan back-edge: dashed, through the left gutter. */}
      {fromNode && toNode && (
        <path
          d={`M ${fromNode.x} ${fromNode.y + NODE_H / 2} H ${BACK_EDGE_X + 6} Q ${BACK_EDGE_X} ${fromNode.y + NODE_H / 2} ${BACK_EDGE_X} ${fromNode.y + NODE_H / 2 - 6} V ${toNode.y + NODE_H / 2 + 6} Q ${BACK_EDGE_X} ${toNode.y + NODE_H / 2} ${BACK_EDGE_X + 6} ${toNode.y + NODE_H / 2} H ${toNode.x - 3}`}
          fill="none"
          strokeDasharray="4 3"
          className="stroke-forge-border"
          markerEnd="url(#dag-arrow-idle)"
        >
          <title>{ui().dagReplanLabel}</title>
        </path>
      )}

      {/* Phase group labels — node-aligned so the gutter stays clear
          for the back-edge. */}
      {groups.map((g) => (
        <text
          key={g.phase}
          x={nodes.find((n) => n.phase === g.phase)?.x ?? 0}
          y={g.y + 17}
          className="fill-forge-text-faint text-[11px] font-medium"
        >
          {g.label}
        </text>
      ))}

      {nodes.map((n) => (
        <DagNode key={n.id} node={n} state={stateOf(n.id)} />
      ))}
    </svg>
  );
}

/** The connected panel: header + scrollable graph. ``flex-1`` (not
 *  ``h-full``) so it fills the DagOverlay's body region; standalone
 *  (tests) it just sizes to content. */
export function DagPanel() {
  const activeNodes = useAppSelector((s) => s.activeNodes);
  const visitedNodes = useAppSelector((s) => s.visitedNodes);
  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {visitedNodes.length === 0 && (
          <p className="px-3 pt-3 text-xs text-forge-text-faint">
            {ui().dagEmptyHint}
          </p>
        )}
        <div className="px-2 py-1">
          <DagGraph activeNodes={activeNodes} visitedNodes={visitedNodes} />
        </div>
      </div>
    </section>
  );
}
