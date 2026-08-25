/**
 * Phase stepper tests — the two renderings of the strip, plus the
 * progress-rail fallback contract:
 *   - LivePhaseStepper (Composer-pinned): null-renders for chat turns,
 *     shows the in-progress step with the pulse while live
 *   - rail fallback: yields whenever ProgressRailContext reports the
 *     right progress rail on screen, surfaces the moment it's away
 *     (collapsed / narrow / no live drill — one boolean decided in
 *     ChatPage)
 *   - history variant (MessageView "phase_stepper" case): the finalised
 *     turn-end summary — failed steps red, untouched steps pending,
 *     no pulse
 * Reducer-level behaviour (gate, seeding, finalise) is covered in
 * core/src/state/reducer.test.ts.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { PhaseStep } from "@blade-ai/core";
import { StoreProvider, configureI18n } from "@blade-ai/core";
import { LivePhaseStepper } from "./PhaseStepper";
import { MessageView } from "./messages";
import { ProgressRailContext } from "../rail/ProgressRailContext";

configureI18n("en");

afterEach(cleanup);

const LIVE_STEPS: PhaseStep[] = [
  { id: "intent", status: "completed" },
  { id: "safety", status: "completed" },
  { id: "inject", status: "in_progress" },
  { id: "verify", status: "pending" },
];

describe("LivePhaseStepper", () => {
  it("renders nothing when no inject/recover turn is in flight", () => {
    const { container } = render(
      <StoreProvider>
        <LivePhaseStepper />
      </StoreProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the live strip with the active step pulsing", () => {
    const { container } = render(
      <StoreProvider initial={{ currentPhaseStepper: { steps: LIVE_STEPS } }}>
        <LivePhaseStepper />
      </StoreProvider>,
    );
    expect(screen.getByText("Intent")).toBeInTheDocument();
    expect(screen.getByText("Safety")).toBeInTheDocument();
    expect(screen.getByText("Inject")).toBeInTheDocument();
    expect(screen.getByText("Verify")).toBeInTheDocument();
    const active = container.querySelector('[data-status="in_progress"]');
    expect(active?.className).toContain("animate-pulse");
    expect(active?.className).toContain("motion-reduce:animate-none");
  });

  it("shows per-step eta: observed duration on completed, running on the live step", () => {
    // Reducer-stamped timestamps only: seed-rounded intent carries
    // none → no eta; safety 6s; the live inject step reads "running".
    render(
      <StoreProvider
        initial={{
          currentPhaseStepper: {
            steps: [
              { id: "intent", status: "completed" },
              {
                id: "safety",
                status: "completed",
                startedAt: 0,
                completedAt: 6_000,
              },
              { id: "inject", status: "in_progress", startedAt: 6_000 },
              { id: "verify", status: "pending" },
            ],
          },
        }}
      >
        <LivePhaseStepper />
      </StoreProvider>,
    );
    expect(screen.getByText("6s")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
  });

  it("yields whenever the right progress rail is on screen", () => {
    // ProgressRailContext = true (drill live + expanded + ≥1250px,
    // decided in ChatPage): the rail's phase panel is the only live
    // phase signal, so the strip renders nothing at all.
    const { container } = render(
      <StoreProvider initial={{ currentPhaseStepper: { steps: LIVE_STEPS } }}>
        <ProgressRailContext.Provider value={true}>
          <LivePhaseStepper />
        </ProgressRailContext.Provider>
      </StoreProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("surfaces at any viewport the moment the rail goes away", () => {
    // Rail away (false = collapsed / narrow / no live drill): the
    // strip is the only live phase signal left and must show.
    const { container } = render(
      <StoreProvider initial={{ currentPhaseStepper: { steps: LIVE_STEPS } }}>
        <ProgressRailContext.Provider value={false}>
          <LivePhaseStepper />
        </ProgressRailContext.Provider>
      </StoreProvider>,
    );
    expect(container).not.toBeEmptyDOMElement();
    expect(screen.getByText("Inject")).toBeInTheDocument();
  });
});

describe("MessageView / phase_stepper history card", () => {
  it("renders the finalised strip without a pulse", () => {
    const { container } = render(
      <StoreProvider>
        <MessageView
          item={{
            kind: "phase_stepper",
            id: "ps-1",
            steps: [
              { id: "intent", status: "completed" },
              { id: "safety", status: "failed" },
              { id: "inject", status: "pending" },
              { id: "verify", status: "pending" },
            ],
          }}
        />
      </StoreProvider>,
    );
    expect(screen.getByText("Safety")).toBeInTheDocument();
    const failed = container.querySelector('[data-status="failed"]');
    expect(failed?.className).toContain("bg-danger");
    expect(
      container.querySelector('[data-status="in_progress"]'),
    ).toBeNull();
    expect(container.querySelector(".animate-pulse")).toBeNull();
  });

  it("renders a recovery-only strip (recover turn)", () => {
    render(
      <StoreProvider>
        <MessageView
          item={{
            kind: "phase_stepper",
            id: "ps-2",
            steps: [{ id: "recovery", status: "completed" }],
          }}
        />
      </StoreProvider>,
    );
    expect(screen.getByText("Recovery")).toBeInTheDocument();
    expect(screen.queryByText("Intent")).toBeNull();
  });

  it("renders observed durations on the finalised strip (never 'running')", () => {
    render(
      <StoreProvider>
        <MessageView
          item={{
            kind: "phase_stepper",
            id: "ps-eta",
            steps: [
              {
                id: "intent",
                status: "completed",
                startedAt: 0,
                completedAt: 65_000,
              },
              {
                id: "safety",
                status: "failed",
                startedAt: 65_000,
                completedAt: 77_000,
              },
            ],
          }}
        />
      </StoreProvider>,
    );
    expect(screen.getByText("1m05s")).toBeInTheDocument();
    expect(screen.getByText("12s")).toBeInTheDocument();
    expect(screen.queryByText("running")).toBeNull();
  });
});
