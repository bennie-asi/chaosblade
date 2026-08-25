/**
 * Full-screen DAG overlay (mock v2 ``#dagOverlay``) — the execution
 * graph on demand, replacing the old always-on right-rail DAG. The
 * graph detail is a drill-down, not a常驻 panel: the ProgressRail's
 * 查看执行图 entry opens it, Esc / backdrop click / the close button
 * dismiss it. Content is the same connected DagPanel.
 */
import { useEffect } from "react";
import { X } from "lucide-react";
import { ui } from "../../lib/uiText";
import { DagPanel } from "./DagPanel";

export function DagOverlay({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label={ui().dagPanelTitle}
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-card border border-forge-border bg-forge-card shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex h-10 shrink-0 items-center justify-between border-b border-forge-border px-3">
          <span className="text-xs font-medium text-forge-text-secondary">
            {ui().dagPanelTitle}
          </span>
          <button
            type="button"
            onClick={onClose}
            title={ui().dagOverlayClose}
            aria-label={ui().dagOverlayClose}
            className="rounded-button p-1 text-forge-text-faint hover:bg-forge-border/60 hover:text-forge-text-secondary"
          >
            <X size={14} />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <DagPanel />
        </div>
      </div>
    </div>
  );
}
