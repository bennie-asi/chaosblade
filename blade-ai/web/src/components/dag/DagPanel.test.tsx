/**
 * DagPanel tests — the execution graph rendered inside the
 * full-screen DagOverlay (mock v2: drill-down from the ProgressRail,
 * no longer an always-on rail).
 *
 * Reducer-level tracking (TURN_STARTED reset, NODE_ENDED delisting,
 * visit dedupe) is covered in core/src/state/reducer.test.ts; the
 * model-level exhaustive pin against graph.py lives in
 * lib/dagModel.test.ts. Here we pin the RENDERING contract:
 * idle/active/done colouring, the empty-state hint, loop badges and
 * group labels. The rail's own collapse round-trip moved to
 * ProgressRail.test.tsx.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { StoreProvider, configureI18n } from "@blade-ai/core";
import { ui } from "../../lib/uiText";
import { DAG_NODES } from "../../lib/dagModel";
import { DagPanel } from "./DagPanel";

configureI18n("en");

afterEach(cleanup);

function renderPanel(initial: { activeNodes?: string[]; visitedNodes?: string[] } = {}) {
  return render(
    <StoreProvider initial={initial}>
      <DagPanel />
    </StoreProvider>,
  );
}

describe("DagPanel", () => {
  it("renders the full 17-node skeleton with captions and group labels", () => {
    renderPanel();
    for (const n of DAG_NODES) {
      expect(screen.getByText(n.id)).toBeInTheDocument();
      expect(screen.getByText(ui()[n.captionKey])).toBeInTheDocument();
    }
    for (const label of ["Intent", "Safety", "Inject", "Verify", "Postmortem", "Recovery"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("shows the empty hint only before any node has been visited", () => {
    const { unmount } = renderPanel();
    expect(screen.getByText(ui().dagEmptyHint)).toBeInTheDocument();
    unmount();
    renderPanel({ visitedNodes: ["agent_loop"] });
    expect(screen.queryByText(ui().dagEmptyHint)).toBeNull();
  });

  it("lights the active node (accent + pulsing dot) and dims done nodes", () => {
    const { container } = render(
      <StoreProvider
        initial={{
          activeNodes: ["execute_loop"],
          visitedNodes: ["agent_loop", "baseline_capture", "execute_loop"],
        }}
      >
        <DagPanel />
      </StoreProvider>,
    );
    const active = container.querySelector('[data-node-id="execute_loop"]');
    expect(active?.getAttribute("data-node-state")).toBe("active");
    expect(active?.querySelector("rect")?.classList.contains("stroke-forge-accent")).toBe(true);
    expect(active?.querySelector("circle.animate-pulse")).not.toBeNull();

    const done = container.querySelector('[data-node-id="agent_loop"]');
    expect(done?.getAttribute("data-node-state")).toBe("done");
    expect(done?.querySelector("circle")?.classList.contains("fill-success-dot")).toBe(true);
    expect(done?.querySelector("circle")?.classList.contains("animate-pulse")).toBe(false);

    const idle = container.querySelector('[data-node-id="se_detect"]');
    expect(idle?.getAttribute("data-node-state")).toBe("idle");
  });

  it("marks exactly the four loop nodes with the round-trip badge", () => {
    const { container } = renderPanel();
    const badged = [...container.querySelectorAll("[data-node-id]")].filter(
      (el) => el.textContent?.includes("↻"),
    );
    expect(badged.map((el) => el.getAttribute("data-node-id")).sort()).toEqual([
      "agent_loop",
      "execute_loop",
      "recover_verifier_loop",
      "verifier_loop",
    ]);
  });

  it("renders the replan back-edge with its a11y label", () => {
    renderPanel();
    expect(screen.getByText(ui().dagReplanLabel)).toBeInTheDocument();
  });
});
