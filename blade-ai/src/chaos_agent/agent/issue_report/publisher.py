"""GitHub issue publishing for failed drills — fire-and-forget.

Semantics mirror the postmortem subsystem: every exception is contained,
the network call has a hard timeout and ZERO retries (bastion /
air-gapped environments must never see a hung drill finalization), and
the drill's result output is never blocked by this step.

The local archive (``~/.blade-ai/issue_reports/<task_id>.json``) is the
durable record: it is written on EVERY attempt outcome (success /
failure / cap) and doubles as the dedup receipt. A missing token never
reaches this module — the gate (should_publish_issue) treats the token
as the on/off switch.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from chaos_agent.utils.time import now_iso

from chaos_agent.agent.issue_report.builder import (
    build_issue_body,
    build_issue_title,
)

logger = logging.getLogger(__name__)

_FILE_MODE = 0o600
_DIR_MODE = 0o700


def get_issue_report_dir() -> Path:
    """Resolve the issue-report archive dir from current settings.

    Follows the same memory_dir convention as postmortems so
    ``BLADE_AI_MEMORY_DIR`` relocates archives too.
    """
    try:
        from chaos_agent.config.settings import settings

        return settings.resolved_memory_dir.parent / "issue_reports"
    except Exception:
        return Path(os.path.expanduser("~/.blade-ai/issue_reports"))


def _archive_path_for(task_id: str, root: Path | None = None) -> Path:
    base = root if root is not None else get_issue_report_dir()
    return base / f"{task_id}.json"


def _read_archive(task_id: str, root: Path | None = None) -> dict | None:
    """Load a previous attempt record for *task_id* (None when absent)."""
    try:
        data = json.loads(
            _archive_path_for(task_id, root).read_text(encoding="utf-8"),
        )
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_archive(task_id: str, record: dict, root: Path | None = None) -> str:
    """Persist the attempt record; returns the path (or '' on failure)."""
    try:
        path = _archive_path_for(task_id, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(_DIR_MODE)
        except OSError:
            pass
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.chmod(_FILE_MODE)
        tmp.replace(path)
        return str(path)
    except Exception as e:
        logger.warning("issue_report: archive write failed for %s: %s", task_id, e)
        return ""


def _ledger_path(root: Path | None = None) -> Path:
    base = root if root is not None else get_issue_report_dir()
    return base / "_ledger.json"


def _daily_count(root: Path | None = None) -> tuple[str, int]:
    """Return (today, published_today) from the per-machine ledger."""
    today = now_iso()[:10]
    try:
        data = json.loads(_ledger_path(root).read_text(encoding="utf-8"))
        if data.get("date") == today:
            return today, int(data.get("count", 0))
    except Exception:
        pass
    return today, 0


def _bump_daily_count(root: Path | None = None) -> None:
    """Increment the published-today counter (successful posts only)."""
    try:
        today, count = _daily_count(root)
        path = _ledger_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"date": today, "count": count + 1}), encoding="utf-8",
        )
    except Exception as e:
        logger.debug("issue_report: ledger bump failed: %s", e)


async def publish_issue_report(
    state: dict,
    task_id: str,
    postmortem_payload: dict | None,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Attempt to publish the failure report as a GitHub issue.

    Returns the payload:
      {"status": "success"|"failed"|"skipped_cap",
       "issue_url"?, "error"?, "message", "archive_path"}
    Never raises.
    """
    from chaos_agent.config.settings import settings

    def _payload(status: str, **extra) -> dict[str, Any]:
        base: dict[str, Any] = {"status": status}
        base.update(extra)
        return base

    try:
        # Dedup receipt: an archive that already records a SUCCESSFUL
        # publish for this task_id means a double finalize (retry /
        # re-entry on the same thread) must NOT post a second issue.
        existing = _read_archive(task_id, root)
        if isinstance(existing, dict) and existing.get("status") == "success":
            return _payload(
                "success",
                issue_url=str(existing.get("issue_url") or ""),
                archive_path=str(_archive_path_for(task_id, root)),
                message="already published; duplicate post skipped",
            )

        token = str(getattr(settings, "github_token", "") or "").strip()
        if not token:
            # Defensive only — should_publish_issue gates on token
            # presence, so a normal run never lands here. Direct calls
            # that skip the gate fail fast without an archive: nothing
            # was attempted.
            return _payload("failed", error="no GitHub token configured")

        today, count = _daily_count(root)
        cap = int(getattr(settings, "issue_report_daily_cap", 5) or 0)
        # cap <= 0 is a hard shut ("zero budget"), NOT unlimited — the
        # cap exists to protect the upstream repo from runaway posts.
        if cap <= 0 or count >= cap:
            archive = _write_archive(task_id, {
                "task_id": task_id,
                "status": "skipped_cap",
                "created_at": now_iso(),
                "title": build_issue_title(state),
            }, root)
            return _payload(
                "skipped_cap",
                message=f"daily issue-report cap reached ({count}/{cap})",
                archive_path=archive,
            )

        title = build_issue_title(state)
        # Archive FIRST (with the full body) so the report survives even
        # when the network call below fails or the process exits early.
        # The SAME record dict is patched in place on the outcome paths
        # below — rebuilding a smaller dict would clobber the durable
        # body the archive contract promises.
        planned_archive = str(_archive_path_for(task_id, root))
        body = build_issue_body(
            state, task_id, postmortem_payload, archive_path=planned_archive,
        )
        record = {
            "task_id": task_id,
            "status": "pending",
            "created_at": now_iso(),
            "repo": settings.issue_report_repo,
            "title": title,
            "body": body,
        }
        archive = _write_archive(task_id, record, root) or planned_archive

        import httpx

        repo = str(settings.issue_report_repo or "chaosblade-io/chaosblade").strip("/")
        url = f"https://api.github.com/repos/{repo}/issues"
        timeout = float(getattr(settings, "issue_report_timeout_seconds", 15) or 15)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    url,
                    json={"title": title, "body": body},
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                        "User-Agent": "blade-ai-issue-report",
                    },
                )
        except httpx.TimeoutException:
            return _payload(
                "failed",
                error=f"timeout after {timeout:.0f}s (network unreachable?)",
                message="GitHub unreachable; report archived locally",
                archive_path=archive,
            )
        except Exception as e:
            return _payload(
                "failed",
                error=f"{type(e).__name__}: {e}",
                message="publish failed; report archived locally",
                archive_path=archive,
            )

        if resp.status_code == 201:
            issue_url = ""
            try:
                issue_url = str(resp.json().get("html_url") or url)
            except Exception:
                issue_url = url
            _bump_daily_count(root)
            # Patch the archived record with the final outcome.
            if archive:
                record["status"] = "success"
                record["issue_url"] = issue_url
                _write_archive(task_id, record, root)
            return _payload("success", issue_url=issue_url, archive_path=archive)

        snippet = (resp.text or "")[:200]
        if archive:
            record["status"] = "failed"
            record["error"] = f"HTTP {resp.status_code}: {snippet}"
            _write_archive(task_id, record, root)
        return _payload(
            "failed",
            error=f"HTTP {resp.status_code}: {snippet}",
            message="publish failed; report archived locally",
            archive_path=archive,
        )
    except Exception as e:
        logger.warning("issue_report: unexpected error for %s: %s", task_id, e)
        return _payload("failed", error=f"{type(e).__name__}: {e}")


__all__ = [
    "get_issue_report_dir",
    "publish_issue_report",
]
