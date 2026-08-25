/**
 * Progress timeline — the right rail's process view (design doc
 * §7.2). ``node_message`` progress lines used to interleave in the
 * chat column; they now live here whenever the rail is visible (the
 * chat column keeps them as the fallback when it isn't — see
 * lib/nodeProgress.ts). Each entry: status dot + source node + the
 * line itself + a timestamp relative to the first entry. New entries
 * rise in once (``.timeline-in``, motion-reduce collapses it); the
 * list sticks to the bottom as entries land.
 *
 * Scope: the LATEST turn only (lib/nodeProgress.ts's
 * latestTurnProgressItems, shared with the countdown anchor) — a
 * previous drill's lines must not parade as the current drill's
 * process view.
 */
import { useEffect, useMemo, useRef } from "react";
import { useAppSelector } from "@blade-ai/core";
import { latestTurnProgressItems } from "../../lib/nodeProgress";
import { ui } from "../../lib/uiText";

/** "+m:ss" against the first timestamped entry. Static per render —
 *  no ticking clock, so re-renders and replay folds stay
 *  deterministic. Negative deltas (mixed server/client clocks) clamp
 *  to zero. */
export function formatRelTs(deltaMs: number): string {
  const totalSec = Math.max(0, Math.round(deltaMs / 1000));
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `+${m}:${String(s).padStart(2, "0")}`;
}

export function TimelinePanel() {
  const history = useAppSelector((s) => s.history);
  const entries = useMemo(() => latestTurnProgressItems(history), [history]);
  const baseTs = entries.find((e) => e.ts !== undefined)?.ts;
  const scrollRef = useRef<HTMLDivElement>(null);

  // Live-appending list: stick to the bottom as entries land.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entries.length]);

  return (
    <section className="flex min-h-24 flex-1 flex-col border-t border-forge-border">
      <header className="flex h-10 shrink-0 items-center border-b border-forge-border px-3">
        <span className="text-xs font-medium text-forge-text-secondary">
          {ui().timelinePanelTitle}
        </span>
      </header>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {entries.length === 0 ? (
          <p className="pt-1 text-xs text-forge-text-faint">
            {ui().timelineEmptyHint}
          </p>
        ) : (
          <ol className="flex flex-col gap-1.5">
            {entries.map((item) => (
              <li
                key={item.id}
                className="timeline-in flex items-start gap-2 text-xs"
              >
                <span className="mt-1 inline-block size-2 shrink-0 rounded-full bg-forge-text-faint" />
                <span className="shrink-0 font-mono text-forge-text-faint">
                  {item.tag}
                </span>
                <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-forge-text-secondary">
                  {item.text}
                </span>
                {item.ts !== undefined && baseTs !== undefined && (
                  <span className="shrink-0 font-mono text-forge-text-faint">
                    {formatRelTs(item.ts - baseTs)}
                  </span>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
