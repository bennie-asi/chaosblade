/**
 * Tasks route — every drill the embedded server has recorded, as a
 * table.
 *
 * Data comes from ``GET /api/v1/metric`` (the same endpoint backing
 * the TUI's ``/tasks``), and the status filter / fault label reuse the
 * core's ``passesTasksFilter`` / ``formatFaultType`` so web and TUI
 * report identical rows on identical data. The list polls every 5s
 * while mounted: drills are long-running and a task's phase moves
 * without any user action on this page.
 *
 * Rows navigate to the task detail route (/tasks/$taskId). A table
 * row can't wrap an <a> (invalid HTML), so navigation goes through
 * row click + Enter on the focused row (role="link" announces the
 * affordance). Trade-off accepted: inline text selection on a row is
 * lost — the dashboard convention.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
  formatFaultType,
  passesTasksFilter,
  type TasksFilter,
} from "@blade-ai/core";
import { useBoot } from "./bootContext";
import { ui } from "../lib/uiText";
import { asString } from "../lib/utils";
import { formatCreated, formatDuration } from "../lib/format";

type TaskRow = Record<string, unknown>;

const REFRESH_MS = 5000;
const FILTERS: readonly TasksFilter[] = ["all", "active", "failed"];

/** Small-dot colour for the status cell. Forge: semantic status is a
 *  dot + text pair, never a filled pill. */
function statusDot(status: string): string {
  switch (status.toLowerCase()) {
    case "success":
      return "bg-success-dot";
    case "failed":
    case "error":
      return "bg-danger";
    case "in_progress":
      return "bg-warning-dot";
    default:
      return "bg-forge-text-faint";
  }
}

/** total_token_input + total_token_output, compacted ("1.5k"). */
function formatTokens(row: TaskRow): string {
  const summary = row["summary"];
  if (!summary || typeof summary !== "object") return "—";
  const s = summary as Record<string, unknown>;
  const total =
    Number(s["total_token_input"] ?? 0) + Number(s["total_token_output"] ?? 0);
  if (!Number.isFinite(total) || total <= 0) return "—";
  return total >= 1000 ? `${(total / 1000).toFixed(1)}k` : String(total);
}

export function TasksPage() {
  const { client } = useBoot();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<TasksFilter>("all");
  const query = useQuery({
    queryKey: ["tasks"],
    queryFn: () => client.listTasks(),
    refetchInterval: REFRESH_MS,
  });

  const tasks = useMemo<TaskRow[]>(() => {
    const list = query.data?.["tasks"];
    return Array.isArray(list) ? (list as TaskRow[]) : [];
  }, [query.data]);

  const visible = tasks.filter((t) => passesTasksFilter(t, filter));

  const filterLabel: Record<TasksFilter, string> = {
    all: ui().tasksFilterAll,
    active: ui().tasksFilterActive,
    failed: ui().tasksFilterFailed,
  };

  let body: React.ReactNode;
  if (query.isPending) {
    body = (
      <p className="py-12 text-center text-sm text-forge-text-faint">
        {ui().tasksLoading}
      </p>
    );
  } else if (query.isError) {
    body = (
      <div className="py-12 text-center">
        <p className="text-sm font-medium text-danger">
          {ui().tasksLoadFailed}
        </p>
        <p className="mt-1 font-mono text-xs text-forge-text-faint">
          {query.error instanceof Error
            ? query.error.message
            : String(query.error)}
        </p>
      </div>
    );
  } else if (tasks.length === 0) {
    body = (
      <p className="py-12 text-center text-sm text-forge-text-faint">
        {ui().tasksEmpty}
      </p>
    );
  } else if (visible.length === 0) {
    body = (
      <p className="py-12 text-center text-sm text-forge-text-faint">
        {ui().tasksEmptyFilter}
      </p>
    );
  } else {
    body = (
      <div className="overflow-x-auto rounded-card border border-forge-border bg-forge-card shadow-card">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-xs text-forge-text-faint">
              <th className="px-4 py-2 font-medium">{ui().tasksColStatus}</th>
              <th className="px-4 py-2 font-medium">{ui().tasksColFault}</th>
              <th className="px-4 py-2 font-medium">{ui().tasksColPhase}</th>
              <th className="px-4 py-2 font-medium">{ui().tasksColDuration}</th>
              <th className="px-4 py-2 font-medium">{ui().tasksColTokens}</th>
              <th className="px-4 py-2 font-medium">{ui().tasksColCreated}</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => {
              const status = asString(row["status"]) || "—";
              const durationMs = Number(row["duration_ms"] ?? NaN);
              const taskId = asString(row["task_id"]);
              const openDetail = () => {
                if (!taskId) return;
                void navigate({
                  to: "/trace/$taskId",
                  params: { taskId },
                });
              };
              return (
                <tr
                  key={taskId || i}
                  role="link"
                  tabIndex={0}
                  onClick={openDetail}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") openDetail();
                  }}
                  className="cursor-pointer border-t border-forge-border hover:bg-forge-bg"
                >
                  <td className="px-4 py-2">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className={`inline-block size-1.5 rounded-full ${statusDot(status)}`}
                      />
                      {status}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {formatFaultType(row) || "—"}
                  </td>
                  <td className="px-4 py-2 text-forge-text-secondary">
                    {asString(row["phase"]) || "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {Number.isFinite(durationMs) && durationMs > 0
                      ? formatDuration(durationMs)
                      : "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {formatTokens(row)}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-forge-text-secondary">
                    {formatCreated(
                      asString(row["gmt_create"]) || asString(row["created_at"]),
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-6 py-6">
        <div className="mb-4 flex items-baseline justify-between">
          <h1 className="text-base font-medium">{ui().tasksTitle}</h1>
          <div className="flex items-center gap-4">
            {FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                aria-pressed={filter === f}
                onClick={() => setFilter(f)}
                className={`border-b-2 pb-0.5 text-sm ${
                  filter === f
                    ? "border-forge-accent text-forge-accent"
                    : "border-transparent text-forge-text-secondary hover:text-forge-text"
                }`}
              >
                {filterLabel[f]}
              </button>
            ))}
          </div>
        </div>
        {body}
      </div>
    </div>
  );
}
