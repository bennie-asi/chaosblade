/**
 * Session panel (design §6 会话历史面板, mock column 2) — the chat
 * route's left-hand session list: time-grouped entries, click to
 * switch, hover ⋯ menu with a two-step delete.
 *
 * DATA HONESTY (tasks.md 阶段2查证 + spec web-navigation-shell):
 * ``GET /api/v1/sessions`` returns the REDUCED subset
 * ``{id, cluster, namespace, model_name, created_at, task_count}`` —
 * no name, no message count. Entry titles are therefore built ONLY
 * from task_count + created_at ("3 tasks · 14:02"); the mock's
 * drill-name rows were illustrative and are deliberately not
 * reproduced.
 *
 * Per-entry drill status dot (the mock's blade-ai increment): derived
 * per session as ``task_ids`` × ``listTasks`` via the core's
 * ``passesTasksFilter`` — live (any in-progress drill, pulsing green)
 * / fail (any failed) / done (the rest). Sessions with no tasks carry
 * no dot. Fetched by a dedicated 10s query alongside the list; a
 * failed probe simply leaves the dot absent.
 *
 * Delete gating (spec 会话删除): the sessions endpoint carries no
 * drill state, so the guard cross-checks ``GET /sessions/{id}/state``
 * ``task_ids`` against ``GET /api/v1/metric`` rows via the core's
 * ``passesTasksFilter(task, "active")`` — the SAME definition of
 * "in progress" the tasks table uses, so the panel and the table can
 * never disagree. The check runs lazily on the first delete click
 * (N sessions must not cost N state fetches just to render).
 *
 * Deleting the ACTIVE session re-homes the chat: newest remaining
 * session via ``switchSession``, or a fresh one via ``resetSession``
 * when the list ran empty. Deleting a background session touches
 * nothing else — and stays available mid-turn, since it can't disturb
 * the in-flight turn's session.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
} from "lucide-react";
import {
  passesTasksFilter,
  useAppSelector,
  type SessionListItem,
} from "@blade-ai/core";
import { useBoot } from "../../app/bootContext";
import { ui } from "../../lib/uiText";
import { asString, cn } from "../../lib/utils";
import { makePersistedFlag } from "../../lib/persistedFlag";

/** Collapse persistence (design §6: 可折叠持久化). */
export const useSessionPanelCollapsed = makePersistedFlag(
  "blade-ai.sessionPanelCollapsed",
);

// ── grouping + titles ───────────────────────────────────────────────

type SessionGroup = "today" | "yesterday" | "week" | "earlier";

const GROUP_ORDER: readonly SessionGroup[] = [
  "today",
  "yesterday",
  "week",
  "earlier",
];

const GROUP_LABEL: Record<SessionGroup, () => string> = {
  today: () => ui().sessionGroupToday,
  yesterday: () => ui().sessionGroupYesterday,
  week: () => ui().sessionGroupWeek,
  earlier: () => ui().sessionGroupEarlier,
};

/** Bucket by LOCAL calendar day, mock v2's four groups. The server's
 *  created_at is already local wall time ("YYYY-MM-DD HH:MM:SS"); the
 *  ``" " → "T"`` swap makes it Date-parseable. Unparseable stamps
 *  fall into "earlier" — the honest bucket when the date is unknown. */
function groupOf(createdAt: string, now: Date): SessionGroup {
  const d = new Date(createdAt.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return "earlier";
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (d >= startToday) return "today";
  if (d.getTime() >= startToday.getTime() - 86_400_000) return "yesterday";
  if (d.getTime() >= startToday.getTime() - 7 * 86_400_000) return "week";
  return "earlier";
}

/** Per-session drill status (mock's entry-prefix dot). */
type DrillDot = "live" | "done" | "fail";

const DRILL_DOT_CLASS: Record<DrillDot, string> = {
  live: "bg-success-dot animate-pulse motion-reduce:animate-none",
  done: "bg-forge-text-faint",
  fail: "bg-danger",
};

const DRILL_DOT_TITLE: Record<DrillDot, () => string> = {
  live: () => ui().sessionDotLive,
  done: () => ui().sessionDotDone,
  fail: () => ui().sessionDotFail,
};

/** ``{n} tasks · HH:MM`` — the only two fields the wire actually
 *  carries. The clock tail is slice-based (local wall time, see
 *  format.ts's formatCreated); a missing/short stamp drops the tail
 *  rather than rendering garbage. */
function entryTitle(s: SessionListItem): string {
  const count =
    s.task_count <= 0
      ? ui().sessionTasksNone
      : s.task_count === 1
        ? ui().sessionTasksOne
        : ui().sessionTasksMany.replace("{n}", String(s.task_count));
  const norm = s.created_at.replace("T", " ");
  const clock = norm.length >= 16 ? norm.slice(11, 16) : "";
  return clock ? `${count} · ${clock}` : count;
}

// ── panel ───────────────────────────────────────────────────────────

/** Per-row delete interaction phase: the ⋯ menu opens ``idle`` (a
 *  plain "delete session" item); the first click runs the active-drill
 *  precheck and either arms the item (second click deletes) or blocks
 *  it with the reason inline. Clicking anywhere else resets to idle. */
type DeletePhase = "idle" | "checking" | "armed" | "blocked";

export function SessionPanel() {
  const { client, activeSessionId, resetSession, switchSession } = useBoot();
  const streamState = useAppSelector((s) => s.streamState);
  const queryClient = useQueryClient();
  const [collapsed, toggleCollapsed] = useSessionPanelCollapsed();

  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [deletePhase, setDeletePhase] = useState<DeletePhase>("idle");
  const menuRef = useRef<HTMLDivElement | null>(null);

  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: () => client.listSessions(),
    // Drills are long-running; the panel polls so another surface's
    // new session (or this one's task_count growing) shows up without
    // a manual refresh.
    refetchInterval: 10_000,
  });

  const idle = streamState === "idle";
  const sessions = sessionsQuery.data ?? [];

  // Per-session drill status dots — same task_ids × listTasks cross
  // the delete precheck uses, polled alongside the list. A failed
  // probe leaves the dot absent (enhancement, never a blocker).
  // Disabled while collapsed: the slim strip shows no dots, so the
  // N+1 requests per tick would be pure waste.
  const statusQuery = useQuery({
    queryKey: ["sessionDrillStatus", sessions.map((s) => s.id).join(",")],
    enabled: sessions.length > 0 && !collapsed,
    refetchInterval: 10_000,
    queryFn: async (): Promise<Record<string, DrillDot>> => {
      const [states, metric] = await Promise.all([
        Promise.allSettled(sessions.map((s) => client.getSessionState(s.id))),
        client.listTasks(),
      ]);
      const rows = Array.isArray(metric["tasks"])
        ? (metric["tasks"] as Record<string, unknown>[])
        : [];
      const next: Record<string, DrillDot> = {};
      states.forEach((r, i) => {
        const s = sessions[i];
        if (!s || r.status !== "fulfilled") return;
        const ids = new Set(
          Array.isArray(r.value["task_ids"])
            ? (r.value["task_ids"] as unknown[]).map(asString)
            : [],
        );
        const mine = rows.filter((t) => ids.has(asString(t["task_id"])));
        if (mine.length === 0) return;
        if (mine.some((t) => passesTasksFilter(t, "active"))) {
          next[s.id] = "live";
        } else if (mine.some((t) => passesTasksFilter(t, "failed"))) {
          next[s.id] = "fail";
        } else {
          next[s.id] = "done";
        }
      });
      return next;
    },
  });
  const dots = statusQuery.data ?? {};
  const closeMenu = () => {
    setMenuFor(null);
    setDeletePhase("idle");
  };

  // Click-outside / Esc closes the menu and disarms the confirm.
  useEffect(() => {
    if (menuFor === null) return;
    const onPointerDown = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) closeMenu();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeMenu();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuFor]);

  const refreshSessions = () =>
    queryClient.invalidateQueries({ queryKey: ["sessions"] });

  const onNewSession = async () => {
    if (!idle || busy) return;
    setError("");
    setBusy(true);
    try {
      await resetSession();
    } catch {
      setError(ui().sessionNewFailed);
    } finally {
      setBusy(false);
      void refreshSessions();
    }
  };

  const onSelect = async (sid: string) => {
    if (sid === activeSessionId || !idle || busy) return;
    setError("");
    setBusy(true);
    try {
      await switchSession(sid);
    } catch {
      // Stale row (deleted elsewhere): switchSession verified the
      // target BEFORE touching local state, so the current session is
      // still intact — just refetch the list so the row disappears.
      setError(ui().sessionSwitchFailed);
      void refreshSessions();
    } finally {
      setBusy(false);
    }
  };

  /** First delete click: the active-drill precheck. Arms the confirm
   *  when the session is clean; blocks inline when it isn't. */
  const runDeletePrecheck = async (sid: string) => {
    setDeletePhase("checking");
    try {
      const [state, metric] = await Promise.all([
        client.getSessionState(sid),
        client.listTasks(),
      ]);
      const ids = new Set(
        Array.isArray(state["task_ids"])
          ? (state["task_ids"] as unknown[]).map(asString)
          : [],
      );
      const rows = Array.isArray(metric["tasks"])
        ? (metric["tasks"] as Record<string, unknown>[])
        : [];
      const hasActiveDrill = rows.some(
        (t) =>
          ids.has(asString(t["task_id"])) && passesTasksFilter(t, "active"),
      );
      setDeletePhase(hasActiveDrill ? "blocked" : "armed");
    } catch {
      // A vanished session can't be deleted anyway — close the menu
      // and let the next poll drop the row.
      closeMenu();
      void refreshSessions();
    }
  };

  const executeDelete = async (sid: string) => {
    setBusy(true);
    setError("");
    try {
      await client.deleteSession(sid);
      closeMenu();
      if (sid === activeSessionId) {
        // Re-home the chat: newest remaining session (the list is
        // newest-first), or a fresh one when nothing is left.
        const rest = await client.listSessions();
        if (rest.length > 0 && rest[0]) {
          await switchSession(rest[0].id);
        } else {
          await resetSession();
        }
      }
    } catch {
      setError(ui().sessionDeleteFailed);
    } finally {
      setBusy(false);
      void refreshSessions();
    }
  };

  if (collapsed) {
    return (
      <aside
        aria-label={ui().sessionsTitle}
        className="flex w-10 shrink-0 flex-col items-center gap-1 border-r border-forge-border bg-forge-sidebar py-2"
      >
        <button
          type="button"
          onClick={toggleCollapsed}
          title={ui().sessionPanelExpand}
          aria-label={ui().sessionPanelExpand}
          className="rounded-button p-1.5 text-forge-text-faint hover:bg-forge-border/60 hover:text-forge-text-secondary"
        >
          <PanelLeftOpen size={14} />
        </button>
        <button
          type="button"
          onClick={onNewSession}
          disabled={!idle || busy}
          title={ui().sessionNew}
          aria-label={ui().sessionNew}
          className="rounded-button p-1.5 text-forge-text-faint hover:bg-forge-border/60 hover:text-forge-text-secondary disabled:opacity-40"
        >
          <Plus size={14} />
        </button>
      </aside>
    );
  }

  const grouped = new Map<SessionGroup, SessionListItem[]>();
  const now = new Date();
  for (const s of sessions) {
    const g = groupOf(s.created_at, now);
    const list = grouped.get(g) ?? [];
    list.push(s);
    grouped.set(g, list);
  }

  return (
    <aside
      aria-label={ui().sessionsTitle}
      className="flex w-60 shrink-0 flex-col border-r border-forge-border bg-forge-sidebar"
    >
      <header className="flex h-10 shrink-0 items-center justify-between px-3">
        <span className="text-xs font-medium text-forge-text-secondary">
          {ui().sessionsTitle}
        </span>
        <button
          type="button"
          onClick={toggleCollapsed}
          title={ui().sessionPanelCollapse}
          aria-label={ui().sessionPanelCollapse}
          className="rounded-button p-1 text-forge-text-faint hover:bg-forge-border/60 hover:text-forge-text-secondary"
        >
          <PanelLeftClose size={14} />
        </button>
      </header>

      <div className="shrink-0 px-2 pb-2">
        <button
          type="button"
          onClick={onNewSession}
          disabled={!idle || busy}
          className="flex w-full items-center justify-center gap-1.5 rounded-button border border-forge-border py-1.5 text-xs text-forge-text-secondary hover:bg-forge-border/40 disabled:opacity-40"
        >
          <Plus size={13} />
          {ui().sessionNew}
        </button>
      </div>

      {error && (
        <p className="shrink-0 px-3 pb-1 text-xs text-danger">{error}</p>
      )}
      {sessionsQuery.isError && (
        <p className="shrink-0 px-3 pb-1 text-xs text-danger">
          {ui().sessionsLoadFailed}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {GROUP_ORDER.filter((g) => grouped.has(g)).map((g) => (
          <section key={g} className="pt-1">
            <div className="px-1 pb-1 text-[11px] font-medium text-forge-text-faint">
              {GROUP_LABEL[g]()}
            </div>
            {(grouped.get(g) ?? []).map((s) => {
              const active = s.id === activeSessionId;
              const menuOpen = menuFor === s.id;
              const dot = dots[s.id];
              return (
                <div
                  key={s.id}
                  className={cn(
                    "group relative flex items-center rounded-button",
                    active && "bg-forge-accent-soft",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => void onSelect(s.id)}
                    disabled={!idle || busy || active}
                    aria-current={active || undefined}
                    title={s.id}
                    className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left disabled:cursor-default"
                  >
                    {dot && (
                      <span
                        data-drill-status={dot}
                        title={DRILL_DOT_TITLE[dot]()}
                        aria-label={DRILL_DOT_TITLE[dot]()}
                        className={cn(
                          "inline-block size-2 shrink-0 rounded-full",
                          DRILL_DOT_CLASS[dot],
                        )}
                      />
                    )}
                    <div className="min-w-0 flex-1">
                      <div
                        className={cn(
                          "truncate text-[13px] leading-5",
                          active
                            ? "text-forge-accent"
                            : "text-forge-text group-hover:text-forge-text",
                        )}
                      >
                        {entryTitle(s)}
                      </div>
                      {(s.namespace || s.cluster) && (
                        <div className="truncate text-[11px] leading-4 text-forge-text-faint">
                          {s.namespace || s.cluster}
                        </div>
                      )}
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (menuOpen) closeMenu();
                      else {
                        setMenuFor(s.id);
                        setDeletePhase("idle");
                      }
                    }}
                    // Deleting the ACTIVE session re-homes the chat —
                    // keep that off the table while a turn is in flight.
                    // Deleting a background session is always safe.
                    disabled={busy || (active && !idle)}
                    title={ui().sessionMore}
                    aria-label={ui().sessionMore}
                    aria-haspopup="menu"
                    aria-expanded={menuOpen}
                    className={cn(
                      "mr-1 rounded-button p-1 text-forge-text-faint hover:bg-forge-border/60 hover:text-forge-text-secondary disabled:opacity-40",
                      menuOpen
                        ? "opacity-100"
                        : "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
                    )}
                  >
                    <MoreHorizontal size={14} />
                  </button>
                  {menuOpen && (
                    <div
                      ref={menuRef}
                      role="menu"
                      className="absolute right-1 top-full z-20 mt-0.5 min-w-40 rounded-button border border-forge-border bg-forge-card py-1 shadow-lg"
                    >
                      {deletePhase === "blocked" ? (
                        <p className="px-3 py-1.5 text-xs text-forge-text-faint">
                          {ui().sessionDeleteBlocked}
                        </p>
                      ) : (
                        <button
                          type="button"
                          role="menuitem"
                          disabled={deletePhase === "checking" || busy}
                          onClick={() => {
                            if (deletePhase === "armed") {
                              void executeDelete(s.id);
                            } else {
                              void runDeletePrecheck(s.id);
                            }
                          }}
                          className={cn(
                            "w-full px-3 py-1.5 text-left text-xs disabled:opacity-40",
                            deletePhase === "armed"
                              ? "text-danger hover:bg-danger/10"
                              : "text-forge-text-secondary hover:bg-forge-border/50",
                          )}
                        >
                          {deletePhase === "armed"
                            ? ui().sessionDeleteArm
                            : ui().sessionDelete}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </section>
        ))}
      </div>
    </aside>
  );
}
