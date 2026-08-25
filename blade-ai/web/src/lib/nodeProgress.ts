/**
 * Node-progress lines (tagged logs): a ``node_message`` commits to
 * history as a LogItem whose ``tag`` is set — slash-command logs stay
 * untagged, so the tag is the precise discriminator. When the right
 * rail is visible (ProgressRailContext) the rail's timeline owns
 * these entries and the chat column drops them; when the rail is
 * away the chat keeps them — the same dynamic fallback rule as
 * LivePhaseStepper, so no live signal ever disappears outright.
 */
import type { HistoryItem } from "@blade-ai/core";

/** Rail visibility breakpoint. ChatPage's useMediaQuery consumes this
 *  constant; keep it aligned with the design doc's 1250px rule (the
 *  Tailwind scanner cannot interpolate constants into class strings,
 *  so any responsive class spelling the same breakpoint stays in sync
 *  by hand). */
export const RAIL_MIN_WIDTH_PX = 1250;

export function isNodeProgressItem(
  item: HistoryItem,
): item is Extract<HistoryItem, { kind: "log" }> {
  return item.kind === "log" && item.tag != null;
}

type ProgressLog = Extract<HistoryItem, { kind: "log" }>;

/** Tagged progress lines of the LATEST turn only. History is
 *  session-scoped and accumulates across drills, so an unfiltered
 *  read shows the previous drill's lines as if they belonged to the
 *  current one (wrong countdown anchor, wrong approved-at, stale
 *  timeline rows). Turn boundary = the last user message; when no
 *  user message exists (tests, freshly hydrated states) the whole
 *  history counts as one turn — matching the pre-filter behaviour
 *  so existing fixtures stay valid. Shared by the rail's countdown
 *  anchor and TimelinePanel so the two never drift apart. */
export function latestTurnProgressItems(history: HistoryItem[]): ProgressLog[] {
  let from = 0;
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i]?.kind === "user") {
      from = i + 1;
      break;
    }
  }
  const out: ProgressLog[] = [];
  for (let i = from; i < history.length; i++) {
    const e = history[i];
    if (e && isNodeProgressItem(e)) out.push(e);
  }
  return out;
}
