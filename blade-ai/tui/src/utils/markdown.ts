/**
 * Render markdown text into ANSI-styled text Ink can display.
 *
 * Pipeline: marked → marked-terminal renderer → ANSI string. Ink's
 * `<Text>` component preserves ANSI escape sequences when present in
 * children, so we pass the rendered string straight through.
 *
 * Caveat: do NOT set ``color`` on the wrapping Text — the inline ANSI
 * escapes carry their own color and Ink would then double-apply on
 * top, producing visible artifacts on some terminals. Let the rendered
 * string own its own colors.
 *
 * Mid-stream behavior: when the agent is still emitting tokens, the
 * input text often ends with a half-formed paragraph or unclosed
 * fence. ``marked`` is permissive and renders partial input as if it
 * were complete; the output flickers slightly as new tokens land but
 * never goes blank.
 *
 * Width handling: ``marked-terminal`` is registered as a global
 * extension on ``marked``, but its width is fixed at registration.
 * We can't re-register on every render (state would clobber on the
 * shared instance), so we keep a tiny LRU of per-width ``Marked``
 * instances. AgentMessage passes the live terminal width; reflowText
 * inside marked-terminal handles paragraph re-flow accordingly.
 */

import { Marked } from "marked";
import { markedTerminal } from "marked-terminal";
import { reflowWideTables, visualLen } from "./tableReflow.js";

const MAX_CACHE = 4;
const _cache = new Map<number, Marked>();

function getMarked(width: number): Marked {
  // Bucket widths so noisy resize events (column-by-column) don't
  // explode the cache. 4-col buckets is fine — marked-terminal's
  // reflow doesn't care about exact width past line-break decisions.
  const bucket = Math.max(20, Math.round(width / 4) * 4);
  const cached = _cache.get(bucket);
  if (cached) {
    // Real LRU: promote to most-recently-used by re-inserting at
    // the end of the Map's insertion order. Without this, a width
    // that's been used continuously can still get evicted because
    // ``_cache.keys().next().value`` returns the oldest *insertion*
    // (FIFO), not the oldest *access*.
    _cache.delete(bucket);
    _cache.set(bucket, cached);
    return cached;
  }

  const m = new Marked();
  m.use(
    markedTerminal({
      reflowText: true,
      width: bucket,
      showSectionPrefix: false,
      tab: 2,
    }) as Parameters<Marked["use"]>[0],
  );

  // Evict the least-recently-used (= oldest insertion among entries
  // that have NOT been promoted) before inserting.
  if (_cache.size >= MAX_CACHE) {
    const oldestKey = _cache.keys().next().value as number | undefined;
    if (oldestKey !== undefined) _cache.delete(oldestKey);
  }
  _cache.set(bucket, m);
  return m;
}

export function renderMarkdown(text: string, width = 80): string {
  if (!text) return "";
  try {
    // marked-terminal draws GFM tables at natural width regardless of
    // the ``width`` option; over-wide tables would then hard-wrap in
    // Ink's <Text wrap="wrap"> and shred the box frame. Downgrade
    // those to a vertical record list before parsing (no-op when the
    // table fits).
    const prepared = reflowWideTables(text, width);
    let result = getMarked(width).parse(prepared, { async: false });
    if (typeof result !== "string") return text;
    // Post-hoc shred guard: ``naturalWidth`` inside tableReflow is a
    // heuristic estimate of marked-terminal's actual box width (per-
    // column padding model). When the estimate undershoots the real
    // rendered width — observed 283 estimated vs 289 actual on a real
    // drill summary table — a terminal width inside the gap window
    // passes the table through unconverted and the 6-char overflow
    // hard-wraps every border line into misaligned fragments. Detect
    // any over-long rendered line that carries box-drawing chars
    // (i.e. an actual table border, not a long code line) and fall
    // back to converting ALL tables to record form: slightly
    // conservative, never shredded. Measure with ``visualLen`` —
    // ``line.length`` would miss CJK-heavy tables whose glyphs take
    // 2 cells but count as 1 JS char.
    const rendered = result.replace(/\x1b\[[0-9;]*m/g, "");
    let shredded = false;
    for (const line of rendered.split("\n")) {
      if (visualLen(line) > width && /[─│┌┐└┘├┤┬┴┼]/.test(line)) {
        shredded = true;
        break;
      }
    }
    if (shredded) {
      const forced = reflowWideTables(text, 0);
      if (forced !== text) {
        const retry = getMarked(width).parse(forced, { async: false });
        if (typeof retry === "string") result = retry;
      }
    }
    // marked-terminal often appends a trailing newline; trim so our
    // Ink <Box marginTop> handles spacing instead.
    return result.replace(/\n+$/, "");
  } catch {
    return text;
  }
}
