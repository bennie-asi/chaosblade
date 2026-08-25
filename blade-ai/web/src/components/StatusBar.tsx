/**
 * Bottom status bar — global facts only (Forge "zero duplication"
 * rule: anything shown here must not get its own panel elsewhere).
 * Left: connection + cluster context. Right: stream state + session.
 */
import { useAppSelector } from "@blade-ai/core";
import { ui } from "../lib/uiText";

export function StatusBar() {
  const session = useAppSelector((s) => s.session);
  const streamState = useAppSelector((s) => s.streamState);

  return (
    <div className="flex h-7 shrink-0 items-center justify-between border-t border-forge-border bg-forge-sidebar px-3 text-xs text-forge-text-faint">
      <div className="flex items-center gap-1.5">
        {/* No status dot here: P1 has no liveness signal to bind it to
            (StreamState has no error/disconnected variant), so a green
            dot would be decoration claiming "connected" — Forge's
            zero-falsehood rule. Re-add when a real signal exists. */}
        {session.cluster ? <span>{session.cluster}</span> : null}
        {session.namespace ? <span>/ {session.namespace}</span> : null}
        {session.modelName ? (
          <span className="text-forge-text-faint">· {session.modelName}</span>
        ) : null}
      </div>
      <div className="flex items-center gap-2 font-mono">
        <span>
          {ui().streamStateLabel}: {streamState}
        </span>
        <span>{session.id.slice(0, 8)}</span>
      </div>
    </div>
  );
}
