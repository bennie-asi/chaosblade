/**
 * LiveThinkingPanel — the web's live chain-of-thought fold.
 *
 * The TUI deliberately does NOT render the live thinking buffer: at
 * 12–17 Hz token arrivals, every re-flow of a multi-row CoT body
 * forced a full dynamic-frame redraw in Ink (see the TUI
 * LoadingIndicator docstring). The web has no such constraint —
 * React diffing + CSS handle a growing text block for free — so the
 * design doc's "信息展示全面升级" applies: the live buffer becomes an
 * expandable panel in the message flow (§6 IA: ``[思考折叠]`` lives
 * inside 消息流, not the chrome).
 *
 * Contract:
 *   - Visible only while ``hasActiveThinking`` (a thinking session is
 *     streaming). On commit the reducer drops the buffer and a
 *     duration-only ``ThinkingItem`` chip lands in the flow — the
 *     committed chip stays collapsed-only BY DESIGN (long CoT is
 *     deliberately not kept in history items; see core types.ts).
 *   - Collapsed by default (动效克制): the header row is a shimmer
 *     "Thinking…" label + ticking elapsed. Click toggles the body.
 *   - Expanded body streams ``thoughtBuffer`` raw — CoT is plain
 *     prose, not markdown; ``whitespace-pre-wrap`` only. Auto-pins
 *     to the bottom while the user stays near it (same contract as
 *     HistoryList), so reading upward mid-stream is never yanked.
 *   - Chevron ▸/▾ mirrors the committed chip's ▸ glyph family.
 */
import { useEffect, useRef, useState } from "react";
import { t, useAppSelector } from "@blade-ai/core";
import { formatDuration } from "../../lib/format";

const NEAR_BOTTOM_PX = 40;

export function LiveThinkingPanel() {
  const active = useAppSelector((s) => s.hasActiveThinking);
  const buffer = useAppSelector((s) => s.thoughtBuffer);
  const startedAt = useAppSelector((s) => s.thoughtStartedAt);
  const [expanded, setExpanded] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const nearBottomRef = useRef(true);

  // Elapsed ticker — the only progress signal while collapsed. 1s is
  // calm; the shimmer already conveys liveness at a glance.
  useEffect(() => {
    if (!active) return;
    const tick = () =>
      setElapsedMs(startedAt > 0 ? Date.now() - startedAt : 0);
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [active, startedAt]);

  // Auto-pin the expanded body while streaming — same near-bottom
  // contract as HistoryList.
  useEffect(() => {
    if (!expanded) return;
    const el = bodyRef.current;
    if (el && nearBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [expanded, buffer]);

  if (!active) return null;

  return (
    <div className="max-w-[85%]">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex items-center gap-1.5 text-xs"
      >
        <span className="text-forge-text-faint">{expanded ? "▾" : "▸"}</span>
        <span className="thinking-shimmer">{t("thinking.live")}</span>
        <span className="text-forge-text-faint">
          · {formatDuration(elapsedMs)}
        </span>
      </button>
      {expanded && (
        <div
          ref={bodyRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            nearBottomRef.current =
              el.scrollHeight - el.scrollTop - el.clientHeight <
              NEAR_BOTTOM_PX;
          }}
          className="mt-1 max-h-60 overflow-y-auto rounded-card border border-forge-border bg-forge-code px-3 py-2 text-xs leading-5 whitespace-pre-wrap text-forge-text-secondary"
        >
          {buffer}
        </div>
      )}
    </div>
  );
}
