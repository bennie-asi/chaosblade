/**
 * Shared mock backend for the web UI tests: stubs ``globalThis.fetch``
 * with a URL router answering the endpoints the app touches
 * (health / create session / list sessions / session state / turn SSE /
 * metric list / metric detail / config read+write / model
 * read+switch).
 *
 * The SSE response carries a REAL ReadableStream — BladeClient reads
 * it via ``body.getReader()``, so a plain string body would bypass the
 * frame parser entirely. Frames are ``data: {json}\n\n`` per the SSE
 * wire format (see client.ts's parseFrame).
 *
 * Sessions are tracked in a live table mirroring the server's
 * in-memory SessionStore: POST appends (first id is MOCK_SESSION_ID,
 * extras are ``sess_extra_N``), DELETE removes, GET /sessions lists
 * newest-first. state/turn match ANY live id so a post-reset session
 * keeps working without per-test rewiring.
 *
 * Callers must restore the real fetch in afterEach.
 */
import { vi } from "vitest";

/** Session id of the FIRST mock session created (subsequent creates
 *  get ``sess_extra_N``) — i.e. the id the app's boot session gets. */
export const MOCK_SESSION_ID = "test-session-id";

export interface MockBackendOptions {
  /** SSE events streamed for POST /sessions/:id/turn. Default: done only. */
  turnEvents?: unknown[];
  /** GET /sessions/:id/state payload. Default: a populated context. */
  sessionState?: Record<string, unknown>;
  /** Per-session state overrides (keyed by session id) — a session
   *  whose state must differ from the default ``sessionState``, e.g.
   *  carrying ``task_ids`` so the delete guard sees an in-flight
   *  drill. Falls back to ``sessionState`` for unlisted ids. */
  sessionStates?: Record<string, Record<string, unknown>>;
  /** Per-creation-order created_at stamps for POST /sessions —
   *  ``createdAts[0]`` is the FIRST session's stamp, etc. Tests use
   *  this to land sessions in specific session-panel groups (today /
   *  yesterday / earlier). Default: synthetic seconds-resolution
   *  stamps on 2026-08-18. */
  sessionCreatedAts?: string[];
  /** When true, POST /sessions answers HTTP 500 (boot-failure path). */
  failCreateSession?: boolean;
  /** GET /api/v1/metric rows. Default: empty list. */
  tasks?: Record<string, unknown>[];
  /** GET /api/v1/metric/{taskId} payloads, keyed by task id. A missing
   *  key answers the server's not-found shape: HTTP 200 with a
   *  ``status: "fail"`` envelope (code 2001). */
  taskDetails?: Record<string, Record<string, unknown>>;
  /** Seed rows for GET /api/v1/config's ``config`` map. POSTs against
   *  /api/v1/config/{key} mutate the same table, so a read-after-write
   *  sees the coerced value — mirroring the real server. */
  config?: Record<string, unknown>;
  /** Keys whose POST answers ``hot_reload: false`` (cold keys, or a
   *  simulated rebuild failure) — drives the amber restart marker. */
  coldKeys?: string[];
  /** When true, POST /sessions/:id/turn answers an SSE stream that
   *  stays open until the client aborts — a turn must genuinely be
   *  in flight for the Stop-button / Esc-cancel tests (useStream's
   *  cancelTurn gates on its abortRef, so a forged ``streamState:
   *  "responding"`` without a real stream would no-op). */
  hangTurn?: boolean;
}

/** Minimal JSON Response stand-in (same pattern as core's client.test.ts). */
export function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => body,
  } as unknown as Response;
}

/** SSE Response stand-in whose stream never closes on its own; it
 *  errors with an AbortError when the request signal aborts — the
 *  same shape a real fetch body takes on ``controller.abort()``. */
export function hangingSseResponse(signal?: AbortSignal | null): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      signal?.addEventListener("abort", () => {
        try {
          controller.error(
            new DOMException("The operation was aborted.", "AbortError"),
          );
        } catch {
          // Already errored/closed — a second abort is a no-op.
        }
      });
    },
  });
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    body: stream,
  } as unknown as Response;
}

/** SSE Response stand-in whose body is a real ReadableStream. */
export function sseResponse(events: unknown[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const evt of events) {
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(evt)}\n\n`),
        );
      }
      controller.close();
    },
  });
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    body: stream,
  } as unknown as Response;
}

/**
 * Install the router on ``globalThis.fetch``. Returns the spy for
 * call-shape inspection when a test needs it.
 */
export function installMockBackend(opts: MockBackendOptions = {}) {
  const turnEvents = opts.turnEvents ?? [{ type: "done" }];
  const sessionState = opts.sessionState ?? {
    cluster: "test-cluster",
    namespace: "test-ns",
    model_name: "test-model",
  };
  // Live-session table (see module docstring). created_at is a
  // synthetic seconds-resolution stamp — enough for newest-first
  // ordering assertions without touching the clock.
  const liveSessions: Array<{
    id: string;
    cluster: string;
    namespace: string;
    model_name: string;
    created_at: string;
    task_count: number;
  }> = [];
  let createdCount = 0;
  // Mutable config table for the settings page: POST writes land here
  // and GET reads back the coerced value, mirroring ConfigStore's
  // read-back-on-write contract (routes/config.py L192-213).
  const configTable: Record<string, unknown> = { ...(opts.config ?? {}) };
  let activeModel = "qwen3.6-max-preview";
  const spy = vi.fn(
    async (url: string | URL | Request, init?: RequestInit) => {
      const u = String(url);
      const method = init?.method ?? "GET";
      if (u.endsWith("/api/v1/health")) {
        return jsonResponse({ status: "ok" });
      }
      if (u.endsWith("/api/v1/sessions") && method === "POST") {
        if (opts.failCreateSession) {
          return jsonResponse({}, 500);
        }
        createdCount += 1;
        const id =
          createdCount === 1 ? MOCK_SESSION_ID : `sess_extra_${createdCount - 1}`;
        liveSessions.push({
          id,
          cluster: String(sessionState["cluster"] ?? ""),
          namespace: String(sessionState["namespace"] ?? ""),
          model_name: String(sessionState["model_name"] ?? ""),
          created_at:
            opts.sessionCreatedAts?.[createdCount - 1] ??
            `2026-08-18 10:00:${String(createdCount).padStart(2, "0")}`,
          task_count: 0,
        });
        return jsonResponse({ session_id: id });
      }
      if (u.endsWith("/api/v1/sessions") && method === "GET") {
        return jsonResponse({
          sessions: [...liveSessions].reverse(),
          total: liveSessions.length,
        });
      }
      if (u.includes("/api/v1/config/")) {
        const key = decodeURIComponent(
          u.split("/api/v1/config/")[1]?.split(/[?#]/)[0] ?? "",
        );
        if (method === "POST") {
          const body = JSON.parse(String(init?.body ?? "{}")) as {
            value?: unknown;
          };
          // Minimal mirror of the server's ``_coerce``: strings arrive
          // verbatim from the client; bool/int-looking strings land
          // typed (routes/config.py's coercion subset the UI emits).
          let value = body.value;
          if (value === "true") value = true;
          else if (value === "false") value = false;
          else if (
            typeof value === "string" &&
            value !== "" &&
            !Number.isNaN(Number(value))
          ) {
            value = Number(value);
          }
          configTable[key] = value;
          const hot = !(opts.coldKeys ?? []).includes(key);
          return jsonResponse({
            status: "success",
            data: {
              key,
              value,
              hot_reload: hot,
              rebuild_error: hot ? null : "simulated rebuild failure",
            },
          });
        }
      }
      if (u.endsWith("/api/v1/config") && method === "GET") {
        return jsonResponse({
          status: "success",
          data: {
            config: { ...configTable },
            config_path: "/tmp/mock-blade-ai/config.json",
          },
        });
      }
      if (u.endsWith("/api/v1/model")) {
        if (method === "GET") {
          return jsonResponse({
            status: "success",
            data: {
              active: activeModel,
              api_base_url: "",
              candidates: [
                { id: "qwen3.6-max-preview", provider: "qwen" },
                { id: "qwen-max", provider: "qwen" },
                { id: "qwen-plus", provider: "qwen" },
              ],
            },
          });
        }
        if (method === "POST") {
          const body = JSON.parse(String(init?.body ?? "{}")) as {
            model_name?: string;
          };
          if (body.model_name) activeModel = body.model_name;
          return jsonResponse({
            status: "success",
            data: {
              active: activeModel,
              restart_required: false,
              rebuild_error: null,
              next_action: "Next /turn will use the new model.",
            },
          });
        }
      }
      if (u.endsWith("/api/v1/metric")) {
        // JSONEnvelope-wrapped, matching the real server — the client's
        // listTasks() unwraps ``data`` before handing it to the page.
        const tasks = opts.tasks ?? [];
        return jsonResponse({
          status: "success",
          data: { tasks, total: tasks.length },
        });
      }
      if (u.includes("/api/v1/metric/")) {
        const taskId = decodeURIComponent(
          u.split("/api/v1/metric/")[1]?.split(/[?#]/)[0] ?? "",
        );
        const detail = opts.taskDetails?.[taskId];
        if (detail) {
          return jsonResponse({ status: "success", data: detail });
        }
        // Mirror the real server: not-found is HTTP 200 + fail envelope.
        return jsonResponse({
          status: "fail",
          code: 2001,
          message: `Task not found: ${taskId}`,
        });
      }
      if (u.includes("/api/v1/sessions/")) {
        if (u.endsWith("/state")) {
          const sid = u.split("/api/v1/sessions/")[1]?.split("/")[0];
          return jsonResponse(
            (sid && opts.sessionStates?.[sid]) ?? sessionState,
          );
        }
        if (u.endsWith("/turn")) {
          if (opts.hangTurn) {
            return hangingSseResponse(init?.signal ?? null);
          }
          return sseResponse(turnEvents);
        }
        if (method === "DELETE") {
          const sid = u.split("/api/v1/sessions/")[1]?.split(/[/?]/)[0];
          const idx = liveSessions.findIndex((s) => s.id === sid);
          if (idx >= 0) liveSessions.splice(idx, 1);
          return jsonResponse({});
        }
      }
      // Everything else (stats patch, config probes from slash
      // handlers, …) gets a benign empty envelope.
      return jsonResponse({});
    },
  );
  globalThis.fetch = spy as unknown as typeof fetch;
  return spy;
}
