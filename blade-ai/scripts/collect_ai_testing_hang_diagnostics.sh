#!/usr/bin/env bash
# Collect read-only diagnostics for an ai-testing resilience run that appears
# stuck. Run this on every application node while the UI is still spinning.

set -uo pipefail

redact_stream() {
  sed -E \
    -e "s/([Aa]uthorization['\"]?[[:space:]]*:[[:space:]]*['\"]?)[^'\"]*/\1<redacted>/g" \
    -e "s/([Aa][Pp][Ii][_-]?[Kk][Ee][Yy]['\"]?[[:space:]]*[:=][[:space:]]*['\"]?)[^'\", }]+/\1<redacted>/g" \
    -e "s/([Aa]ccess[_-]?[Kk]ey[_-]?[Ss]ecret['\"]?[[:space:]]*[:=][[:space:]]*['\"]?)[^'\", }]+/\1<redacted>/g" \
    -e "s/([Bb]earer[[:space:]]+)[A-Za-z0-9._~+\/-]+/\1<redacted>/g"
}

flatten_diagnostics() {
  local source_dir="$1"
  local output_file="$2"
  local file relative

  {
    echo "AI-TESTING HANG DIAGNOSTICS"
    echo "generated_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "source_dir=${source_dir}"
    echo
    while IFS= read -r file; do
      relative="${file#${source_dir}/}"
      echo "================================================================================"
      echo "BEGIN FILE: ${relative}"
      echo "================================================================================"
      cat "${file}" 2>&1 | redact_stream || true
      echo
      echo "================================================================================"
      echo "END FILE: ${relative}"
      echo "================================================================================"
      echo
    done < <(find "${source_dir}" -type f -print | LC_ALL=C sort)
  } >"${output_file}"
}

RUN_ID="${RUN_ID:-${1:-}}"
PLATFORM_TASK_ID="${PLATFORM_TASK_ID:-${2:-}}"
APP_ROOT="${APP_ROOT:-/home/admin/ai-testing/target/ai-testing}"
BACKEND_DIR="${BACKEND_DIR:-${APP_ROOT}/backend}"
PYTHON_BIN="${PYTHON_BIN:-${APP_ROOT}/.venv/bin/python3}"
LOG_DIR="${LOG_DIR:-/home/admin/ai-testing/logs}"
LOG_LINES="${LOG_LINES:-5000}"
COMPACT_MODE="${COMPACT_MODE:-1}"
MAX_RELEVANT_LOG_LINES="${MAX_RELEVANT_LOG_LINES:-8000}"
MAX_INFRA_LOG_LINES="${MAX_INFRA_LOG_LINES:-1200}"
MAX_FD_LINES="${MAX_FD_LINES:-300}"
MAX_SOCKET_LINES="${MAX_SOCKET_LINES:-1000}"
WATCH_MODE="${WATCH_MODE:-0}"
STALL_SECONDS="${STALL_SECONDS:-120}"
WATCH_SECONDS="${WATCH_SECONDS:-1800}"
POLL_SECONDS="${POLL_SECONDS:-2}"
RUN_MATCH="${RUN_MATCH:-}"

if [[ -z "${RUN_ID}" && "${WATCH_MODE}" == "1" ]]; then
  RUN_ID="auto"
fi

if [[ -z "${RUN_ID}" ]]; then
  echo "Usage: RUN_ID=<agent-run-uuid> [PLATFORM_TASK_ID=<inject-task-uuid>] $0"
  echo "   or: $0 <agent-run-uuid> [inject-task-uuid]"
  echo "Watch: WATCH_MODE=1 RUN_ID=auto $0"
  exit 2
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python not found or not executable: ${PYTHON_BIN}"
  exit 2
fi

find_app_context_pid() {
  local child context_pid master_pid

  context_pid=""
  master_pid="$(pgrep -fo 'uvicorn app\.main:app' 2>/dev/null || true)"
  if [[ -n "${master_pid}" ]]; then
    for child in $(pgrep -P "${master_pid}" 2>/dev/null || true); do
      if tr '\0' ' ' <"/proc/${child}/cmdline" 2>/dev/null \
        | grep -q 'multiprocessing\.spawn.*spawn_main'; then
        context_pid="${child}"
        break
      fi
    done
    context_pid="${context_pid:-${master_pid}}"
  fi
  printf '%s' "${context_pid}"
}

run_in_app_context() {
  local script_path="$1"
  local context_pid

  context_pid="$(find_app_context_pid)"
  if [[ -n "${context_pid}" && -r "/proc/${context_pid}/environ" ]]; then
    APP_CONTEXT_PID="${context_pid}" PROBE_SCRIPT="${script_path}" \
      "${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

import os
import runpy
import sys

preserve_names = {
    "RUN_ID",
    "SDK_TASK_ID",
    "STALL_SECONDS",
    "WATCH_SECONDS",
    "POLL_SECONDS",
    "RUN_MATCH",
    "WATCH_EVIDENCE_DIR",
    "APP_CONTEXT_PID",
    "PROBE_SCRIPT",
}
preserved = {key: value for key, value in os.environ.items() if key in preserve_names}
pid = preserved["APP_CONTEXT_PID"]

with open(f"/proc/{pid}/environ", "rb") as fh:
    for item in fh.read().split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        os.environ[key.decode(errors="surrogateescape")] = value.decode(
            errors="surrogateescape"
        )

os.environ.update(preserved)
cwd = os.readlink(f"/proc/{pid}/cwd")
os.chdir(cwd)
sys.path.insert(0, cwd)
for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    if entry and entry not in sys.path:
        sys.path.insert(0, entry)

runpy.run_path(preserved["PROBE_SCRIPT"], run_name="__main__")
PY
    return
  fi

  (
    cd "${BACKEND_DIR}" || exit 1
    "${PYTHON_BIN}" "${script_path}"
  )
}

WATCH_EVIDENCE_DIR=""
if [[ "${WATCH_MODE}" == "1" ]]; then
  WATCH_HOST="$(hostname 2>/dev/null || echo unknown-host)"
  WATCH_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  WATCH_EVIDENCE_DIR="/tmp/ai-testing-watch-${WATCH_HOST}-${WATCH_STAMP}"
  mkdir -p "${WATCH_EVIDENCE_DIR}"

  cat >"${WATCH_EVIDENCE_DIR}/watch_probe.py" <<'PY'
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path


async def main() -> int:
    import asyncpg
    import redis.asyncio as aioredis
    from app.config import get_settings

    requested = os.environ.get("RUN_ID", "auto")
    run_match = os.environ.get("RUN_MATCH", "")
    stall_seconds = float(os.environ.get("STALL_SECONDS", "120"))
    watch_seconds = float(os.environ.get("WATCH_SECONDS", "1800"))
    poll_seconds = float(os.environ.get("POLL_SECONDS", "2"))
    out = Path(os.environ["WATCH_EVIDENCE_DIR"])
    settings = get_settings()

    db = await asyncpg.connect(
        settings.langgraph_postgres_url,
        timeout=10,
        command_timeout=15,
    )
    redis_client = None
    pubsub = None
    selected = ""
    status = ""
    reason = ""
    saw_session_end = False
    token_event_count = 0
    started = time.monotonic()
    last_event = started
    db_started_at = await db.fetchval("SELECT clock_timestamp()::timestamp")

    def log(message: str) -> None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        line = f"{stamp} {message}"
        print(line, flush=True)
        with (out / "watch.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    try:
        log(
            f"watch started requested={requested} stall={stall_seconds}s "
            f"duration={watch_seconds}s match={run_match!r}"
        )
        while time.monotonic() - started < watch_seconds:
            if not selected:
                if requested not in ("auto", "latest"):
                    row = await db.fetchrow(
                        "SELECT id::text, status FROM agent_runs WHERE id::text = $1",
                        requested,
                    )
                elif requested == "latest":
                    row = await db.fetchrow(
                        """
                        SELECT id::text, status FROM agent_runs
                        WHERE status = 'running'
                          AND ($1 = '' OR raw_input ILIKE '%' || $1 || '%')
                        ORDER BY started_at DESC LIMIT 1
                        """,
                        run_match,
                    )
                else:
                    row = await db.fetchrow(
                        """
                        SELECT id::text, status FROM agent_runs
                        WHERE status = 'running' AND started_at >= $1
                          AND ($2 = '' OR raw_input ILIKE '%' || $2 || '%')
                        ORDER BY started_at ASC LIMIT 1
                        """,
                        db_started_at,
                        run_match,
                    )
                if row is None:
                    await asyncio.sleep(poll_seconds)
                    continue
                selected = row["id"]
                status = row["status"]
                (out / "selected_run_id").write_text(selected, encoding="utf-8")
                log(f"attached run_id={selected} status={status}")
                last_event = time.monotonic()
                try:
                    redis_client = aioredis.from_url(
                        settings.REDIS_URL,
                        encoding="utf-8",
                        decode_responses=True,
                        socket_connect_timeout=5,
                        socket_timeout=10,
                    )
                    await redis_client.ping()
                    pubsub = redis_client.pubsub()
                    await pubsub.subscribe(f"agentprog:{selected}")
                    log(f"redis subscribed channel=agentprog:{selected}")
                except Exception as exc:
                    log(f"redis subscribe failed: {type(exc).__name__}: {exc}")
                    pubsub = None

            row = await db.fetchrow(
                """
                SELECT status, started_at, updated_at, completed_at, error
                FROM agent_runs WHERE id::text = $1
                """,
                selected,
            )
            if row is None:
                reason = "agent_run_disappeared"
                log(reason)
                return 42
            status = row["status"]

            if pubsub is not None:
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=min(1.0, poll_seconds),
                    )
                    if message and message.get("type") == "message":
                        raw = message.get("data", "")
                        last_event = time.monotonic()
                        try:
                            event = json.loads(raw)
                        except Exception:
                            event = {}
                        event_type = event.get("type", "")
                        phase = event.get("phase", "")
                        kind = (event.get("extras") or {}).get("event_kind", "")
                        if kind == "llm_token":
                            token_event_count += 1
                            if token_event_count % 100 == 0:
                                log(f"llm_token heartbeat count={token_event_count}")
                        else:
                            with (out / "redis-events.jsonl").open(
                                "a", encoding="utf-8"
                            ) as fh:
                                fh.write(str(raw).replace("\n", "\\n") + "\n")
                            log(f"event type={event_type} phase={phase} kind={kind}")
                        if event_type == "session_end":
                            saw_session_end = True
                except Exception as exc:
                    log(f"redis read failed: {type(exc).__name__}: {exc}")
                    try:
                        await pubsub.aclose()
                    except Exception:
                        pass
                    pubsub = None

            if status != "running":
                if saw_session_end:
                    reason = f"completed_normally status={status}"
                    log(reason)
                    return 0
                reason = f"terminal_without_session_end status={status}"
                log(reason)
                return 42

            silent_for = time.monotonic() - last_event
            if silent_for >= stall_seconds:
                reason = f"event_silence status=running silent_seconds={silent_for:.1f}"
                log(reason)
                return 42

            await asyncio.sleep(max(0.1, poll_seconds))

        reason = f"watch_timeout seconds={watch_seconds} status={status or 'unknown'}"
        log(reason)
        return 43
    finally:
        (out / "watch-summary.json").write_text(
            json.dumps(
                {
                    "run_id": selected,
                    "status": status,
                    "reason": reason,
                    "saw_session_end": saw_session_end,
                    "token_event_count": token_event_count,
                    "watch_elapsed_seconds": round(time.monotonic() - started, 3),
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        if pubsub is not None:
            try:
                await pubsub.aclose()
            except Exception:
                pass
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                pass
        await db.close()


raise SystemExit(asyncio.run(main()))
PY

  echo "Watch mode active; waiting for an intermittent stall..."
  RUN_ID="${RUN_ID}" STALL_SECONDS="${STALL_SECONDS}" \
    WATCH_SECONDS="${WATCH_SECONDS}" POLL_SECONDS="${POLL_SECONDS}" \
    RUN_MATCH="${RUN_MATCH}" WATCH_EVIDENCE_DIR="${WATCH_EVIDENCE_DIR}" \
    run_in_app_context "${WATCH_EVIDENCE_DIR}/watch_probe.py"
  WATCH_RESULT=$?
  rm -f "${WATCH_EVIDENCE_DIR}/watch_probe.py"

  if [[ -r "${WATCH_EVIDENCE_DIR}/selected_run_id" ]]; then
    RUN_ID="$(cat "${WATCH_EVIDENCE_DIR}/selected_run_id")"
  fi

  if [[ "${WATCH_RESULT}" -ne 42 || -z "${RUN_ID}" || "${RUN_ID}" == "auto" ]]; then
    WATCH_OUTPUT="${WATCH_EVIDENCE_DIR}.txt"
    flatten_diagnostics "${WATCH_EVIDENCE_DIR}" "${WATCH_OUTPUT}"
    rm -rf "${WATCH_EVIDENCE_DIR}"
    echo "Watch ended without a stall trigger (code=${WATCH_RESULT})."
    echo "Watch diagnostics: ${WATCH_OUTPUT}"
    exit "${WATCH_RESULT}"
  fi

  echo "Stall trigger detected for run_id=${RUN_ID}; collecting full snapshot."
fi

SDK_TASK_ID=""
if [[ -n "${PLATFORM_TASK_ID}" ]]; then
  if [[ "${PLATFORM_TASK_ID}" == task-* ]]; then
    SDK_TASK_ID="${PLATFORM_TASK_ID}"
  else
    SDK_TASK_ID="task-${PLATFORM_TASK_ID//-/}"
  fi
fi

HOST="$(hostname 2>/dev/null || echo unknown-host)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/tmp/ai-testing-hang-${HOST}-${RUN_ID}-${STAMP}"
mkdir -p "${OUT}"

if [[ -n "${WATCH_EVIDENCE_DIR}" && -d "${WATCH_EVIDENCE_DIR}" ]]; then
  cp -R "${WATCH_EVIDENCE_DIR}" "${OUT}/watch"
fi

echo "Collecting diagnostics into ${OUT}"
echo "Run ID: ${RUN_ID}"
echo "Platform task ID: ${PLATFORM_TASK_ID:-unknown}"
echo "SDK task ID: ${SDK_TASK_ID:-unknown}"

{
  echo "collected_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "hostname=${HOST}"
  echo "run_id=${RUN_ID}"
  echo "platform_task_id=${PLATFORM_TASK_ID}"
  echo "sdk_task_id=${SDK_TASK_ID}"
  echo "app_root=${APP_ROOT}"
  uname -a
  uptime
  echo
  echo "===== process tree ====="
  ps -efww
  echo
  command -v pstree >/dev/null 2>&1 && pstree -ap || true
  echo
  echo "===== resource snapshot ====="
  command -v free >/dev/null 2>&1 && free -m || true
  df -h
  command -v top >/dev/null 2>&1 && top -b -n 1 || true
  echo
  echo "===== relevant child commands ====="
  pgrep -af 'uvicorn|multiprocessing.spawn|resource_tracker|wiz|kubectl|blade|idp-server|uniagent' || true
  echo
  echo "===== sockets ====="
  command -v ss >/dev/null 2>&1 && ss -tpna | sed -n "1,${MAX_SOCKET_LINES}p" || true
  echo
  echo "===== cgroup ====="
  for f in /sys/fs/cgroup/memory.events /sys/fs/cgroup/memory.current \
           /sys/fs/cgroup/memory.max /sys/fs/cgroup/cpu.stat; do
    [[ -r "${f}" ]] && { echo "--- ${f}"; cat "${f}"; }
  done
} >"${OUT}/system.txt" 2>&1

MASTER_PID="$(pgrep -fo 'uvicorn app.main:app' 2>/dev/null || true)"
PIDS=""
if [[ -n "${MASTER_PID}" ]]; then
  PIDS="${MASTER_PID} $(pgrep -P "${MASTER_PID}" 2>/dev/null || true)"
fi
# Include detached/draining Uvicorn workers too. A worker that lost its
# supervisor can still own an in-memory AgentRun and its network sockets; those
# are often the most important processes in a production-only hang.
PIDS="${PIDS} $(pgrep -f 'multiprocessing\.spawn.*spawn_main' 2>/dev/null || true)"
PIDS="$(printf '%s\n' ${PIDS} 2>/dev/null | awk 'NF && !seen[$1]++ {print $1}')"

mkdir -p "${OUT}/processes"
for pid in ${PIDS}; do
  [[ -d "/proc/${pid}" ]] || continue
  {
    echo "===== ps ====="
    ps -o pid,ppid,lstart,etime,stat,wchan:32,cmd -p "${pid}" -ww || true
    echo "===== status ====="
    cat "/proc/${pid}/status" 2>/dev/null || true
    echo "===== limits ====="
    cat "/proc/${pid}/limits" 2>/dev/null || true
    echo "===== io ====="
    cat "/proc/${pid}/io" 2>/dev/null || true
    echo "===== thread wait channels ====="
    for task in /proc/"${pid}"/task/*; do
      tid="${task##*/}"
      printf 'tid=%s wchan=' "${tid}"
      cat "${task}/wchan" 2>/dev/null || echo unavailable
    done
    echo "===== file descriptors ====="
    printf 'fd_count='
    find "/proc/${pid}/fd" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l || true
    ls -l "/proc/${pid}/fd" 2>/dev/null | sed -n "1,${MAX_FD_LINES}p" || true
  } >"${OUT}/processes/proc-${pid}.txt" 2>&1

  if command -v py-spy >/dev/null 2>&1; then
    if command -v timeout >/dev/null 2>&1; then
      timeout 20 py-spy dump --pid "${pid}" \
        >"${OUT}/processes/py-spy-${pid}.txt" 2>&1 || true
    else
      py-spy dump --pid "${pid}" \
        >"${OUT}/processes/py-spy-${pid}.txt" 2>&1 || true
    fi
  fi
done

cat >"${OUT}/runtime_probe.py" <<'PY'
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit


def safe_url(value: str) -> str:
    if not value:
        return ""
    try:
        p = urlsplit(value)
        host = p.hostname or ""
        port = f":{p.port}" if p.port else ""
        return f"{p.scheme}://{host}{port}{p.path}"
    except Exception:
        return "<configured>"


def emit(label: str, value) -> None:
    print(f"{label}={json.dumps(value, default=str, ensure_ascii=False)}")


async def main() -> None:
    run_id = os.environ["RUN_ID"]
    sdk_task_id = os.environ.get("SDK_TASK_ID", "")

    from app.config import get_settings
    platform = get_settings()

    import chaos_agent
    from chaos_agent.config.settings import settings as blade

    emit("blade_ai_version", importlib.metadata.version("blade-ai"))
    emit("chaos_agent_path", chaos_agent.__file__)
    emit("redis_url", safe_url(platform.REDIS_URL))
    emit("database_url", safe_url(platform.DATABASE_URL))
    emit("platform_offline", platform.PLATFORM_OFFLINE)
    emit("observability_enabled", platform.OBSERVABILITY_ENABLED)
    emit("blade_model", blade.model_name)
    emit("blade_api_base", safe_url(blade.api_base_url))
    emit("blade_llm_connect_timeout", blade.llm_connect_timeout)
    emit("blade_llm_read_timeout", blade.llm_read_timeout)
    emit("blade_llm_max_retries", blade.llm_max_retries)
    emit("blade_max_inject_seconds", blade.max_inject_seconds)
    emit("blade_tool_timeouts", {
        "blade": blade.timeout_blade,
        "kubectl": blade.timeout_kubectl,
        "kubectl_exec": blade.timeout_kubectl_exec,
    })
    emit("blade_default_storage", {
        "checkpoint_backend": blade.checkpoint_backend,
        "tasks_db_backend": blade.tasks_db_backend,
    })
    emit("arms_child_process", os.environ.get("APSARA_APM_INSTRUMENTATION_CHILD_PROCESS", ""))

    print("\n===== DNS / LLM endpoint probe (no API key sent) =====")
    try:
        host = urlsplit(blade.api_base_url).hostname or ""
        emit("llm_dns", sorted({x[4][0] for x in socket.getaddrinfo(host, 443)}))
    except Exception as exc:
        emit("llm_dns_error", f"{type(exc).__name__}: {exc}")
    try:
        import httpx
        url = blade.api_base_url.rstrip("/") + "/models"
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await client.get(url)
        emit("llm_http_probe", {
            "status_code": response.status_code,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
    except Exception as exc:
        emit("llm_http_probe_error", f"{type(exc).__name__}: {exc}")

    print("\n===== Redis =====")
    redis_client = None
    try:
        import redis.asyncio as aioredis
        redis_client = aioredis.from_url(
            platform.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=10,
        )
        emit("redis_ping", await redis_client.ping())
        emit("redis_numsub", await redis_client.pubsub_numsub(f"agentprog:{run_id}"))
        info = await redis_client.info("clients")
        emit("redis_clients", {
            "connected_clients": info.get("connected_clients"),
            "blocked_clients": info.get("blocked_clients"),
        })
    except Exception as exc:
        emit("redis_error", f"{type(exc).__name__}: {exc}")
    finally:
        if redis_client is not None:
            close = getattr(redis_client, "aclose", None) or getattr(redis_client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    print("\n===== PostgreSQL =====")
    connection = None
    try:
        import asyncpg
        connection = await asyncpg.connect(
            platform.langgraph_postgres_url,
            timeout=10,
            command_timeout=15,
        )

        async def query(label: str, sql: str, *args) -> list:
            try:
                rows = await connection.fetch(sql, *args)
                emit(label, [dict(row) for row in rows])
                return list(rows)
            except Exception as exc:
                emit(label + "_error", f"{type(exc).__name__}: {exc}")
                return []

        agent_rows = await query(
            "agent_run",
            """
            SELECT id::text, session_id::text, status, trace_id, error,
                   started_at, updated_at, completed_at,
                   left(task_results::text, 10000) AS task_results
            FROM agent_runs WHERE id::text = $1
            """,
            run_id,
        )
        session_id = str(agent_rows[0]["session_id"]) if agent_rows else ""

        if session_id:
            print("\n===== Blade dialogue session files =====")
            session_root = Path(blade.resolved_memory_dir) / "sessions"
            session_files = {}
            for suffix in (".json", ".jsonl"):
                path = session_root / f"{session_id}{suffix}"
                if path.exists():
                    stat = path.stat()
                    session_files[suffix] = {
                        "path": str(path),
                        "size_bytes": stat.st_size,
                        "mtime": stat.st_mtime,
                    }
            emit("blade_dialogue_files", session_files)
        if sdk_task_id:
            await query(
                "chaos_task",
                """
                SELECT task_id, task_state, stage, phase, operation, skill_name,
                       blade_uid, namespace, target_name, tenant_id, error,
                       finished_at, duration_ms, gmt_create, gmt_modified
                FROM tasks WHERE task_id = $1
                """,
                sdk_task_id,
            )
            await query(
                "chaos_task_detail",
                """
                SELECT task_id, safety_status, needs_confirm,
                       length(fault_spec) AS fault_spec_bytes,
                       length(verification) AS verification_bytes,
                       length(result) AS result_bytes,
                       length(failure_reason) AS failure_reason_bytes,
                       total_token_input, total_token_output, total_llm_calls,
                       total_tool_calls, total_duration_ms, gmt_modified
                FROM task_details WHERE task_id = $1
                """,
                sdk_task_id,
            )
            await query(
                "chaos_task_spans",
                """
                SELECT task_id, node_name, start_time, end_time, duration_ms,
                       token_input, token_output,
                       left(tool_calls, 4000) AS tool_calls, error
                FROM task_spans WHERE task_id = $1 ORDER BY id
                """,
                sdk_task_id,
            )

        thread_ids = [run_id]
        if session_id:
            thread_ids.append(f"chaos-{session_id}")
        if sdk_task_id:
            thread_ids.append(sdk_task_id)

        for thread_id in thread_ids:
            await query(
                f"checkpoint_summary:{thread_id}",
                """
                SELECT thread_id, checkpoint_ns, count(*) AS checkpoint_count,
                       max(checkpoint_id) AS latest_checkpoint_id
                FROM checkpoints WHERE thread_id = $1
                GROUP BY thread_id, checkpoint_ns
                """,
                thread_id,
            )
            await query(
                f"checkpoint_latest:{thread_id}",
                """
                SELECT checkpoint_id,
                       metadata->>'source' AS source,
                       metadata->>'step' AS step,
                       checkpoint->>'ts' AS checkpoint_ts,
                       checkpoint->'channel_versions' AS channel_versions,
                       checkpoint->'versions_seen' AS versions_seen
                FROM checkpoints
                WHERE thread_id = $1
                ORDER BY checkpoint_id DESC LIMIT 1
                """,
                thread_id,
            )
            await query(
                f"checkpoint_writes:{thread_id}",
                """
                SELECT thread_id, checkpoint_ns, channel, count(*) AS write_count
                FROM checkpoint_writes WHERE thread_id = $1
                GROUP BY thread_id, checkpoint_ns, channel
                ORDER BY write_count DESC
                """,
                thread_id,
            )

        await query(
            "pg_activity",
            """
            SELECT application_name, coalesce(client_addr::text, 'local') AS client_addr,
                   state, wait_event_type, wait_event, count(*) AS connections
            FROM pg_stat_activity
            WHERE datname = current_database()
            GROUP BY application_name, client_addr, state, wait_event_type, wait_event
            ORDER BY connections DESC
            """,
        )
        await query(
            "pg_locks",
            """
            SELECT mode, granted, count(*) AS lock_count
            FROM pg_locks
            WHERE database = (SELECT oid FROM pg_database WHERE datname = current_database())
            GROUP BY mode, granted ORDER BY lock_count DESC
            """,
        )
    except Exception as exc:
        emit("postgres_error", f"{type(exc).__name__}: {exc}")
    finally:
        if connection is not None:
            await connection.close()


asyncio.run(main())
PY

RUN_ID="${RUN_ID}" SDK_TASK_ID="${SDK_TASK_ID}" \
  run_in_app_context "${OUT}/runtime_probe.py" \
  >"${OUT}/runtime-and-storage.txt" 2>&1 || true

for name in application.log error.log deploy.log; do
  if [[ -r "${LOG_DIR}/${name}" ]]; then
    tail -n "${LOG_LINES}" "${LOG_DIR}/${name}" \
      >"${OUT}/${name}.tail" 2>&1 || true
  fi
done

{
  grep -aF -C 12 "${RUN_ID}" "${OUT}"/*.tail 2>&1 || true
  if [[ -n "${SDK_TASK_ID}" ]]; then
    grep -aF -C 12 "${SDK_TASK_ID}" "${OUT}"/*.tail 2>&1 || true
  fi
} | tail -n "${MAX_RELEVANT_LOG_LINES}" >"${OUT}/target-run-log-lines.txt"

INFRA_PATTERN='Redis listener|Redis PUBLISH|AgentProgressBus|agentprog:|Future attached to a different loop|publish timeout|timed out|TimeoutError|asyncpg|psycopg|SQLAlchemy|Checkpointer|LLM slot acquired|openai\._base_client.*HTTP Response|llm_(start|end|error)|tool_(start|end)|INVOKE_FAILED'
{
  grep -aE "${INFRA_PATTERN}" "${OUT}"/*.tail 2>&1 || true
} | tail -n "${MAX_INFRA_LOG_LINES}" >"${OUT}/infrastructure-log-lines.txt"

if [[ "${COMPACT_MODE}" == "1" ]]; then
  rm -f "${OUT}"/*.tail
fi

{
  command -v circusctl >/dev/null 2>&1 && circusctl status || true
  echo
  curl -sS --max-time 5 -w '\nhttp_code=%{http_code} total=%{time_total}\n' \
    http://127.0.0.1:8000/api/health || true
} >"${OUT}/service-health.txt" 2>&1

rm -f "${OUT}/runtime_probe.py"
OUTPUT_FILE="${OUT}.txt"
flatten_diagnostics "${OUT}" "${OUTPUT_FILE}"
rm -rf "${OUT}"
if [[ -n "${WATCH_EVIDENCE_DIR}" && -d "${WATCH_EVIDENCE_DIR}" ]]; then
  rm -rf "${WATCH_EVIDENCE_DIR}"
fi

echo
echo "Diagnostic file created: ${OUTPUT_FILE}"
echo "Compact mode: ${COMPACT_MODE}"
echo "Run the same command on every node and provide all diagnostic files."
