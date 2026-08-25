/**
 * Environment-neutral perf-trace sink.
 *
 * Core modules (reducer, useStream) emit perf marks through this file so
 * @blade-ai/core stays free of Node-only dependencies (``node:fs``,
 * ``process.stdout`` …). The default sink is an inlinable no-op — a
 * browser bundle pays literally nothing.
 *
 * A host with a real backend (the TUI's ``utils/perfTrace.ts``, which
 * appends JSONL to ``~/.blade-ai/logs/``) installs it at startup via
 * ``setPerfSink`` — module side effect, so a single
 * ``import "./utils/perfTrace.js"`` anywhere in the host's import graph
 * wires every core call site up.
 */

export interface PerfSink {
  /** Point-in-time event (e.g. ``token.raw``). */
  mark(label: string, payload?: Record<string, unknown>): void;
  /** Wrap a synchronous body and record its duration. */
  span<T>(label: string, fn: () => T, payload?: Record<string, unknown>): T;
  /** Force-flush buffered marks (turn boundaries). */
  flush(reason: string): void;
}

const noopSink: PerfSink = {
  mark: () => undefined,
  span: <T>(_label: string, fn: () => T): T => fn(),
  flush: () => undefined,
};

let sink: PerfSink = noopSink;

/** Install (or clear, with ``null``) the host's perf backend. */
export function setPerfSink(next: PerfSink | null): void {
  sink = next ?? noopSink;
}

/** Same call signature as the TUI's historical perfTrace.perfMark. */
export function perfMark(label: string, payload?: Record<string, unknown>): void {
  sink.mark(label, payload);
}

/** Same call signature as the historical perfSpan. */
export function perfSpan<T>(
  label: string,
  fn: () => T,
  payload?: Record<string, unknown>,
): T {
  return sink.span(label, fn, payload);
}

/** Same call signature as the historical perfFlush. */
export function perfFlush(reason: string): void {
  sink.flush(reason);
}
