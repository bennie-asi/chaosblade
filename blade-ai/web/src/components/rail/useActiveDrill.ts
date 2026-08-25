/**
 * useActiveDrill — the right progress rail's data spine.
 *
 * Candidate task: the in-flight turn's taskId when one exists, else
 * the last committed turn's lastTaskId (RESULT_RECEIVED keeps it, so
 * a just-finished drill stays on screen until its metric reaches a
 * terminal state). The metric envelope is polled while the drill is
 * live and stops once ``task_state`` turns terminal — no background
 * churn for a settled task.
 *
 * "Live" has two honest sources, checked in order:
 *   1. the metric's ``task_state`` is non-terminal (the authoritative
 *      server-side lifecycle), or
 *   2. the metric hasn't landed yet / failed to load but a turn is in
 *      flight or a live phase stepper exists (the client-side signal
 *      that something is happening right now).
 * Without either, the rail renders nothing (§7.2: 无任务时整栏不渲染).
 */
import { useQuery } from "@tanstack/react-query";
import { useAppSelector, type BladeClient } from "@blade-ai/core";
import { asString } from "../../lib/utils";

/** task_state values that mean the drill is still on the cluster
 *  (``state.py`` lifecycle: injecting → injected → recovering are the
 *  only non-terminal drill states). */
export const NON_TERMINAL_TASK_STATES = ["injecting", "injected", "recovering"];

const POLL_MS = 5000;

export interface ActiveDrill {
  /** Candidate task the rail describes — undefined when the session
   *  has produced no task yet. */
  taskId: string | undefined;
  /** Metric envelope (raw passthrough record); undefined while the
   *  first poll is in flight or when the fetch failed. */
  metric: Record<string, unknown> | undefined;
  /** Whether the rail should be on screen at all (before collapse /
   *  viewport gating). */
  isLive: boolean;
}

export function useActiveDrill(client: BladeClient): ActiveDrill {
  const taskId = useAppSelector((s) => s.taskId);
  const lastTaskId = useAppSelector((s) => s.lastTaskId);
  const streamState = useAppSelector((s) => s.streamState);
  const stepper = useAppSelector((s) => s.currentPhaseStepper);
  const candidateId = taskId ?? lastTaskId;

  const query = useQuery({
    queryKey: ["activeDrill", candidateId],
    queryFn: () => client.getTaskMetric(candidateId as string),
    enabled: candidateId !== undefined,
    refetchInterval: (q) => {
      const m = q.state.data as Record<string, unknown> | undefined;
      // Keep polling until the server reports a terminal state; an
      // unanswered / failed fetch stays on the clock (drill could be
      // mid-lifecycle).
      if (!m) return POLL_MS;
      return NON_TERMINAL_TASK_STATES.includes(asString(m["task_state"]))
        ? POLL_MS
        : false;
    },
  });

  const metric = query.data as Record<string, unknown> | undefined;
  const serverLive =
    metric !== undefined &&
    NON_TERMINAL_TASK_STATES.includes(asString(metric["task_state"]));
  const clientLive = streamState !== "idle" || stepper !== null;
  const isLive = candidateId !== undefined && (serverLive || (metric === undefined && clientLive));

  return { taskId: candidateId, metric, isLive };
}
