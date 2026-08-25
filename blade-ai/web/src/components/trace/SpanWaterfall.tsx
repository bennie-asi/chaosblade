/**
 * Node-span waterfall — the trace page's execution-time view.
 *
 * Geometry: t0 = first span's start_time; window covers every span's
 * actual END — max(offset + duration) — which intentionally deviates
 * from the CLI's formula (max-offset + LAST span's duration): that
 * one under-covers when the max-offset span isn't the longest-running
 * (the CLI compensates with a min() clip; with the correct window the
 * clip is pure defence).
 *
 * Bar colour semantics (Forge palette, aligned with the CLI renderer):
 * - red        — span.error set (except interruption)
 * - grey       — interrupted span (error contains "interrupt")
 * - accent橙   — node consumed LLM tokens (the model was on this node)
 * - success绿  — clean tool/deterministic node
 *
 * Row meta: duration · tokens ↓↑ · tool calls aggregated (name×n).
 * An error row expands in place to show the full error text; the row
 * tail carries the ✗ / ⏸ glyph so colour is never the only signal.
 *
 * The summary line MUST stay arithmetically consistent with the rows:
 * span count, window, token sums, tool-call total and the confirm-gate
 * share are all derived from the same `ordered` array the rows render.
 */
import { useState } from "react";
import { ui } from "../../lib/uiText";
import { asString } from "../../lib/utils";
import { formatDuration } from "../../lib/format";

export type Span = Record<string, unknown>;

/** Confirmation-gate node names share this substring across graphs
 *  (inject + recover); the gate's duration share is the "human wait"
 *  figure shown in the summary line. */
const CONFIRM_GATE = "confirmation_gate";

function spanTokenIn(s: Span): number {
  return Number(s["token_input"] ?? 0);
}
function spanTokenOut(s: Span): number {
  return Number(s["token_output"] ?? 0);
}
function spanTools(s: Span): string[] {
  return Array.isArray(s["tool_calls"]) ? (s["tool_calls"] as string[]) : [];
}

/** Aggregate a tool-call name list into ``name×count`` segments, in
 *  first-seen order. */
function toolSummary(tools: string[]): string {
  const counts = new Map<string, number>();
  for (const t of tools) counts.set(t, (counts.get(t) ?? 0) + 1);
  return [...counts.entries()].map(([n, c]) => `${n}×${c}`).join(" ");
}

function OneRow({
  span,
  t0,
  windowMs,
}: {
  span: Span;
  t0: number;
  windowMs: number;
}) {
  const [open, setOpen] = useState(false);
  const offsetMs = (Number(span["start_time"] ?? 0) - t0) * 1000;
  const dur = Number(span["duration_ms"] ?? 0);
  const leftPct = (offsetMs / windowMs) * 100;
  // With the max-end window, left+width ≤ 100 holds by construction;
  // the min() is kept as cheap insurance against dirty span data
  // (negative durations, clock skew), matching the CLI's clip.
  const widthPct = Math.min((dur / windowMs) * 100, 100 - leftPct);
  const error = asString(span["error"]);
  const interrupted = error.includes("interrupt");
  const hasLlm = spanTokenIn(span) > 0 || spanTokenOut(span) > 0;
  const barColor = interrupted
    ? "bg-forge-text-faint"
    : error
      ? "bg-danger"
      : hasLlm
        ? "bg-forge-accent"
        : "bg-success-dot";
  const nameColor = interrupted
    ? "text-forge-text-faint"
    : error
      ? "text-danger"
      : "";

  const meta: string[] = [formatDuration(dur)];
  const tokIn = spanTokenIn(span);
  const tokOut = spanTokenOut(span);
  if (tokIn || tokOut) meta.push(`${tokIn}↓${tokOut}↑`);
  const tools = spanTools(span);
  if (tools.length > 0) meta.push(toolSummary(tools));
  const glyph = interrupted ? "⏸" : error ? "✗" : "";

  return (
    <div className="py-0.5">
      <div
        className={`flex items-center gap-3 ${
          error && !interrupted ? "cursor-pointer" : ""
        }`}
        onClick={
          error && !interrupted
            ? () => {
                setOpen((v) => !v);
              }
            : undefined
        }
        // A div is used as the toggle because the row is a flex layout
        // with an absolute-positioned bar; a <button> would fight the
        // baseline alignment. Keyboard access arrives with the row's
        // focus handler below.
        role={error && !interrupted ? "button" : undefined}
        tabIndex={error && !interrupted ? 0 : undefined}
        aria-expanded={error && !interrupted ? open : undefined}
        onKeyDown={
          error && !interrupted
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  setOpen((v) => !v);
                }
              }
            : undefined
        }
      >
        <span
          className={`w-36 shrink-0 truncate text-sm ${nameColor}`}
          title={asString(span["node_name"]) || undefined}
        >
          {asString(span["node_name"]) || "?"}
        </span>
        <div className="relative h-2 min-w-0 flex-1">
          <div
            className={`absolute h-full rounded-sm ${barColor}`}
            style={{
              left: `${leftPct}%`,
              width: `${widthPct}%`,
              minWidth: 2,
            }}
          />
        </div>
        <span className="w-56 shrink-0 truncate text-right font-mono text-xs text-forge-text-faint">
          {meta.join(" · ")}
        </span>
        <span className="w-4 shrink-0 text-center text-xs">
          {glyph && (
            <span className={error && !interrupted ? "text-danger" : "text-forge-text-faint"}>
              {glyph}
            </span>
          )}
        </span>
      </div>
      {open && error && !interrupted && (
        <pre className="mt-1 mb-1.5 ml-36 whitespace-pre-wrap rounded-md border border-danger/40 bg-forge-surface p-2 font-mono text-xs text-danger">
          {error}
        </pre>
      )}
    </div>
  );
}

export function SpanWaterfall({ spans }: { spans: Span[] }) {
  if (spans.length === 0) {
    return (
      <p className="mt-1 text-sm text-forge-text-faint">
        {ui().taskDetailSpansNone}
      </p>
    );
  }
  const ordered = [...spans].sort(
    (a, b) => Number(a["start_time"] ?? 0) - Number(b["start_time"] ?? 0),
  );
  const t0 = Number(ordered[0]?.["start_time"] ?? 0);
  const windowMs = Math.max(
    1,
    ...ordered.map(
      (s) =>
        (Number(s["start_time"] ?? 0) - t0) * 1000 +
        Number(s["duration_ms"] ?? 0),
    ),
  );

  // Summary figures derive from the same ordered array the rows use —
  // they can never drift apart.
  const tokIn = ordered.reduce((acc, s) => acc + spanTokenIn(s), 0);
  const tokOut = ordered.reduce((acc, s) => acc + spanTokenOut(s), 0);
  const toolTotal = ordered.reduce((acc, s) => acc + spanTools(s).length, 0);
  const confirmMs = ordered
    .filter((s) => asString(s["node_name"]).includes(CONFIRM_GATE))
    .reduce((acc, s) => acc + Number(s["duration_ms"] ?? 0), 0);
  const confirmPct = Math.round((confirmMs / windowMs) * 100);

  const summaryParts: string[] = [
    `${ordered.length} ${ui().traceSummaryNodes}`,
    `Σ ${formatDuration(windowMs)}`,
  ];
  if (tokIn || tokOut) {
    summaryParts.push(`tokens ${tokIn}↓${tokOut}↑`);
  }
  if (toolTotal > 0) {
    summaryParts.push(`${ui().traceSummaryTools} ×${toolTotal}`);
  }
  if (confirmMs > 0) {
    summaryParts.push(
      `${ui().traceSummaryConfirm} ${formatDuration(confirmMs)} (${confirmPct}%)`,
    );
  }

  return (
    <div className="mt-1">
      <p className="text-xs text-forge-text-faint">{summaryParts.join(" · ")}</p>
      {/* Axis ends: 0 on the left, window on the right. Intermediate
          ticks buy nothing at this density — the per-row durations
          carry the numbers. */}
      <div className="mt-2 flex justify-between font-mono text-[10px] text-forge-text-faint">
        <span>0s</span>
        <span>{formatDuration(windowMs)}</span>
      </div>
      <div>
        {ordered.map((s, i) => (
          <OneRow key={i} span={s} t0={t0} windowMs={windowMs} />
        ))}
      </div>
    </div>
  );
}
