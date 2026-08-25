/**
 * Turn-end token usage summary row.
 *
 * Visual: ``⚡ turn used 287 tokens (in 198, out 89, 08-05
 * 14:32:07)`` in dim secondary color, sitting at the bottom of the
 * turn's scrollback block — just above the next user prompt. This is
 * the authoritative per-turn count, sourced from server ``usage`` SSE
 * events (LangChain ``on_chat_model_end``) and aggregated across every
 * LLM call within the turn (intent + tool-loop + final reply).
 *
 * Suppressed entirely (item never created) when both counts are 0,
 * so older servers without the ``usage`` event produce turns
 * identical to the prior shape. See ``commitPending`` in reducer.ts.
 */

import { Box, Text } from "ink";
import { memo } from "react";
import { t } from "@blade-ai/core";
import { Theme } from "../../theme/colors.js";
import type { TurnUsageItem } from "@blade-ai/core";

/** Format a raw token count for compact display. ≥1000 collapses to
 *  ``X.Yk`` so a chunky ``"6273 tokens"`` line shrinks to ``"6.3k
 *  tokens"`` — easier to scan at a glance, leaves more horizontal room
 *  for the rest of the row. Sub-1000 stays exact (no point rounding
 *  ``287`` to ``0.3k``). One decimal place keeps small movements
 *  visible (``1.2k`` vs ``1.3k``); cap at one decimal so the row
 *  width is predictable across orders of magnitude. */
function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(1)}k`;
}

/** Local-timezone ``MM-DD HH:MM:SS`` (no ``timeZone`` option —
 *  Intl then uses the host's zone, so the stamp matches whatever
 *  clock the operator is looking at).
 *  ``formatToParts`` + manual assembly pins the ``MM-DD HH:MM:SS``
 *  order (Intl locales disagree on date-part ordering). Built once
 *  at module level: DateTimeFormat construction is comparatively
 *  expensive and this row renders for every committed turn. */
const LOCAL_TIME_FMT = new Intl.DateTimeFormat("en-GB", {
  hour12: false,
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function formatLocalTime(ms: number): string {
  const parts: Record<string, string> = {};
  for (const p of LOCAL_TIME_FMT.formatToParts(ms)) {
    parts[p.type] = p.value;
  }
  return `${parts["month"]}-${parts["day"]} ${parts["hour"]}:${parts["minute"]}:${parts["second"]}`;
}

const TurnUsageMessageInternal: React.FC<{ item: TurnUsageItem }> = ({
  item,
}) => {
  const total = formatTokens(item.inputTokens + item.outputTokens);
  const input = formatTokens(item.inputTokens);
  const output = formatTokens(item.outputTokens);
  const time = formatLocalTime(item.endedAt);
  return (
    <Box paddingLeft={2} marginTop={1}>
      <Text color={Theme.text.secondary}>
        {`⚡ ${t("turn.usage", { total, input, output, time })}`}
      </Text>
    </Box>
  );
};

// React.memo: TurnUsageItem is created at TURN_DONE and never mutated;
// shallow compare on ``item`` ref is the right gate.
export const TurnUsageMessage = memo(TurnUsageMessageInternal);
