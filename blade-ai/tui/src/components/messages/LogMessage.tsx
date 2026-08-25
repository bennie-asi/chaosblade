/**
 * Log line renderer. Two distinct shapes:
 *
 * · Slash-command output (no ``tag``) — leaderless, level-tinted
 *   plain text. Same as before; ``/help`` / ``/tasks`` etc.
 *
 * · Node progress readout (``tag`` set, from ``node_message``) —
 *   a cool reticle readout at a deeper indent:
 *
 *       ◎ Checking target health…
 *       ◎ Assessing injection feasibility…
 *
 *   The ``◎`` reticle (forge.gold — molten gold, hue-shifted from
 *   fire's red-orange) reads as the instrument taking a sighting on
 *   the target: same warm temperature family as the ⏺ agent leader
 *   but a distinct hue, so system readouts never get mistaken for the
 *   agent's voice. (An earlier cool ``status.info`` tint read as a
 *   foreign temperature against the all-warm stream, the desaturated
 *   ``forge.dim`` read muddy and hard to see on light terminals, and
 *   full ``forge.fire`` erased the ◎/⏺ speaker distinction.) Indent 2
 *   aligns the reticle with the thinking (▸) and agent (⏺) leaders —
 *   one shared left rail for every conversation element. Every
 *   readout carries the same one-row top spacer as the other message
 *   kinds — progress lines never glue to the content above or to each
 *   other. Body uses the terminal's default foreground
 *   (``text.primary``), exactly like the agent's streaming reply, so
 *   readouts read as first-class content rather than faded annotation.
 *
 * Renders simple markdown-style emphasis (``**bold**``) inline;
 * otherwise plain text. Doesn't go through marked because callers
 * emit only one or two emphasis spans per line — a tiny inline
 * parser is cheaper.
 */

import { Box, Text } from "ink";
import { memo } from "react";
import type { LogItem } from "@blade-ai/core";
import { Theme } from "../../theme/colors.js";
import { Icons } from "../../theme/icons.js";

function levelColor(level: LogItem["level"]): string | undefined {
  switch (level) {
    case "warn":
      return Theme.status.warn;
    case "ok":
      return Theme.status.ok;
    default:
      return Theme.text.primary;
  }
}

/**
 * Split text into runs alternating plain / bold for ``**…**`` markers.
 * ``\\*`` literal stars are preserved. No fancy emphasis nesting.
 */
function splitBold(text: string): Array<{ text: string; bold: boolean }> {
  const out: Array<{ text: string; bold: boolean }> = [];
  let i = 0;
  let buf = "";
  while (i < text.length) {
    if (text[i] === "\\" && text[i + 1] === "*") {
      buf += "*";
      i += 2;
      continue;
    }
    if (text[i] === "*" && text[i + 1] === "*") {
      // Find closing.
      const close = text.indexOf("**", i + 2);
      if (close < 0) {
        buf += "**";
        i += 2;
        continue;
      }
      if (buf) out.push({ text: buf, bold: false });
      out.push({ text: text.slice(i + 2, close), bold: true });
      buf = "";
      i = close + 2;
      continue;
    }
    buf += text[i];
    i += 1;
  }
  if (buf) out.push({ text: buf, bold: false });
  return out;
}

const LogMessageInternal: React.FC<{ item: LogItem }> = ({ item }) => {
  const lines = item.text.split("\n");

  // Node progress readout — reticle leader aligned with the
  // thinking / agent left rail (indent 2). Carries the same one-row
  // top spacer every other conversation element gets: progress lines
  // never glue to the content above or to each other.
  if (item.tag) {
    return (
      <Box paddingLeft={2} marginTop={1} flexDirection="row">
        <Text color={Theme.forge.gold}>{Icons.scope} </Text>
        <Box flexDirection="column" flexGrow={1}>
          {lines.map((line, i) => (
            <Box key={`${item.id}-${i}`}>
              <Text color={Theme.text.primary}>
                {splitBold(line).map((r, j) =>
                  r.bold ? (
                    <Text key={j} bold>
                      {r.text}
                    </Text>
                  ) : (
                    <Text key={j}>{r.text}</Text>
                  ),
                )}
              </Text>
            </Box>
          ))}
        </Box>
      </Box>
    );
  }

  // Slash-command output — leaderless, level-tinted.
  const color = levelColor(item.level);
  return (
    <Box paddingLeft={2} marginTop={1} flexDirection="column">
      {lines.map((line, i) => {
        const runs = splitBold(line);
        return (
          <Box key={`${item.id}-${i}`}>
            <Text color={color}>
              {runs.map((r, j) =>
                r.bold ? (
                  <Text key={j} bold>
                    {r.text}
                  </Text>
                ) : (
                  <Text key={j}>{r.text}</Text>
                ),
              )}
            </Text>
          </Box>
        );
      })}
    </Box>
  );
};

// React.memo: per-line splitBold parser is non-trivial — multi-line
// /help / /tasks output runs the parser N times per render. Item ref
// is stable post-dispatch; default shallow compare prevents repeat
// work.
export const LogMessage = memo(LogMessageInternal);
