"""Decision gate + issue payload assembly for drill-failure issue reports.

Pure functions only — no I/O, no network. The publisher (``publisher.py``)
performs the actual GitHub POST and local archiving.

Privacy contract (mirrors the postmortem subsystem):
- Everything embedded in the issue body passes through :func:`redact`
  first — Authorization headers, tokens, passwords and kubeconfig
  credential blobs are stripped.
- The issue publishes to a PUBLIC repository: content is permanent and
  searchable. The body carries ONLY the postmortem markdown (its own
  Timeline / Key Metrics / Verifier Findings sections already contain
  the execution evidence) plus small environment metadata; the complete
  execution record stays local (``<memory_dir>/tasks/<task_id>.json``)
  and is referenced by path, never uploaded. Conversation messages are
  deliberately NOT scanned — nothing from ``state["messages"]`` ever
  enters an issue.
"""

from __future__ import annotations

import logging
import platform
import re

logger = logging.getLogger(__name__)

# GitHub issue body hard limit is 65536 chars; leave headroom for the
# API envelope / escaping.
MAX_BODY_CHARS = 60_000

# Postmortem budget — the LLM post-mortem is the ONLY diagnostic
# payload of the issue; it lands intact up to this cap before the
# belt-and-braces body slice could ever touch it.
MAX_POSTMORTEM_CHARS = 30_000

_TRUNCATION_MARKER = (
    "\n\n---\n> ⚠️ Body truncated at 60000 chars. The complete execution "
    "record is archived locally on the reporter's machine."
)

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_REDACTION_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # Authorization headers (curl -H / HTTP traces)
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[\w.\-/+]{8,}"),
        r"\1[REDACTED]",
    ),
    # GitHub tokens (classic PAT / fine-grained / oauth)
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GH_TOKEN]"),
    # key: value style secrets (yaml / json / env)
    (
        re.compile(
            r'(?i)(["\']?(?:token|api[_-]?key|secret|passwd|password)'
            r'["\']?\s*[:=]\s*)(["\']?)([^\s"\',}{]{4,})\2'
        ),
        r"\1\2[REDACTED]\2",
    ),
    # CLI flag style (--password xxx / --token xxx)
    (
        re.compile(r"(?i)(--(?:password|token|api-key|secret)[=\s]+)(\S+)"),
        r"\1[REDACTED]",
    ),
    # kubeconfig credential blobs — base64 data fields
    (
        re.compile(r"(?i)((?:client-certificate-data|client-key-data|token)\s*:\s*)([A-Za-z0-9+/=]{16,})"),
        r"\1[REDACTED]",
    ),
)


def redact(text: str) -> str:
    """Strip known credential shapes from *text*.

    Best-effort by design: it removes the common patterns (bearer
    headers, PATs, password/token fields, kubeconfig data blobs). It is
    NOT a guarantee that no sensitive value survives — that is why the
    embedded payload is a summary, and the settings carry a privacy
    warning.
    """
    if not text:
        return text
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def should_publish_issue(state: dict, settings) -> bool:
    """Decide whether this task warrants an issue-report attempt.

    Conditions (ALL must hold):
      1. ``settings.github_token`` is configured — the
         token IS the switch: configured = on, empty = off. There is
         deliberately no separate ``enabled`` field.
      2. Task is an inject (not chat / recover-bridge)
      3. ``terminal_task_state(state) == "failed"`` — the SAME function
         the result card's FAILED badge derives its ``task_state`` from
         (operation_result.build_inject_data_from_state). One predicate,
         one source of truth: the card shows FAILED if and only if an
         issue report is attempted.
      4. Failure category is NOT on the issue skip list — the
         postmortem skip categories (user_rejected / safety_rejected:
         the user or a guardrail refused BEFORE execution, guardrails
         working as intended, not a drill failure) PLUS
         ``planning_timeout`` (a budget/config exhaustion: max planning
         iterations hit before any plan existed to reject — a resource
         issue, not a drill finding worth a GitHub report). Note the
         asymmetry is deliberate: planning_timeout still gets a LOCAL
         postmortem (postmortem skip list unchanged), it just never
         publishes.
    """
    token = str(getattr(settings, "github_token", "") or "")
    if not token.strip():
        return False
    if state.get("confirmed_intent") not in ("inject",):
        return False
    from chaos_agent.agent.state import terminal_task_state

    if terminal_task_state(dict(state)) != "failed":
        return False

    from chaos_agent.agent.postmortem.builder import _POSTMORTEM_SKIP_CATEGORIES
    from chaos_agent.agent.result.operation_outcome import read_operation_outcome
    from chaos_agent.agent.result.verdict import FailureCategory

    # Issue skip list = postmortem skip list + planning_timeout (see
    # docstring condition 4 for the asymmetry rationale).
    _issue_skip = _POSTMORTEM_SKIP_CATEGORIES | frozenset({
        FailureCategory.PLANNING_TIMEOUT.value,
    })

    failure_detail = read_operation_outcome(dict(state)).failure_detail or {}
    category = failure_detail.get("category") if isinstance(failure_detail, dict) else None
    if category and category in _issue_skip:
        return False
    return True


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

def _fault_triple(state: dict) -> tuple[str, str, str]:
    """scope / fault_target / fault_action from the canonical FaultSpec.

    Goes through ``read_fault_spec`` so the dict↔instance contract and
    the legacy scattered-field projection (old checkpoints carrying
    ``state.target`` / ``state.fault_target`` instead of a fault_spec
    dict) live in one place.
    """
    from chaos_agent.agent.spec.fault_spec import read_fault_spec

    spec = read_fault_spec(state)
    if spec is not None:
        return (
            spec.scope or "?",
            spec.fault_target or "?",
            spec.fault_action or "?",
        )
    return ("?", "?", "?")


def _blade_ai_version() -> str:
    try:
        from chaos_agent import __version__

        return __version__
    except Exception:
        return "unknown"


def build_issue_title(state: dict) -> str:
    """One-line, label-free title (external users cannot create labels)."""
    from chaos_agent.agent.result.operation_outcome import read_operation_outcome

    outcome = read_operation_outcome(state)
    category = "unknown"
    detail = outcome.failure_detail
    if isinstance(detail, dict) and detail.get("category"):
        category = str(detail["category"])
    scope, target, action = _fault_triple(state)
    return f"[blade-ai drill failure] {category} — {scope}/{target}/{action}"


def build_issue_body(
    state: dict,
    task_id: str,
    postmortem_payload: dict | None,
    *,
    archive_path: str = "",
) -> str:
    """Assemble the (redacted, size-capped) issue body.

    The postmortem markdown IS the diagnostic payload — its own
    Timeline / Key Metrics / Verifier Findings sections carry the
    execution evidence, so the body embeds nothing else: no
    message-scanned tool timeline, no result-envelope summary.
    """
    from chaos_agent.config.settings import settings
    from chaos_agent.transports.registry import resolve_channel_name

    postmortem_md = ""
    if isinstance(postmortem_payload, dict):
        postmortem_md = str(postmortem_payload.get("markdown") or "")

    env_lines = [
        f"- blade-ai version: {_blade_ai_version()}",
        f"- model: {getattr(settings, 'model_name', '') or 'unknown'}",
        f"- channel: {resolve_channel_name(state)}",
        f"- os: {platform.system()} {platform.machine()}",
        f"- task_id: {task_id}",
    ]

    local_refs = [
        f"- postmortem: {postmortem_payload.get('path', '') if isinstance(postmortem_payload, dict) else '(not generated)'}",
        # SessionStore convention — see memory/session_store.py: one
        # file per task under ``<memory_dir>/tasks/``. Resolved (not
        # hardcoded ~) so a custom BLADE_AI_MEMORY_DIR stays accurate.
        f"- execution record: {settings.resolved_memory_dir / 'tasks' / (task_id + '.json')}",
    ]
    if archive_path:
        local_refs.append(f"- issue archive: {archive_path}")

    postmortem_section = redact(postmortem_md) if postmortem_md else "_(not generated)_"

    env_block = "\n".join(env_lines)
    refs_block = "\n".join(local_refs)
    # Shrink an oversized postmortem into the room left under the body
    # cap (never beyond its own budget); fixed overhead here is tiny,
    # so in practice the 30K postmortem budget always wins.
    fixed_len = len(env_block) + len(refs_block) + 100  # headings headroom
    available = max(0, MAX_BODY_CHARS - fixed_len)
    pm_budget = min(MAX_POSTMORTEM_CHARS, available) if postmortem_md else 0
    if postmortem_md and len(postmortem_section) > pm_budget:
        postmortem_section = (
            postmortem_section[:pm_budget]
            + f"\n\n… postmortem truncated ({len(postmortem_md) - pm_budget} chars omitted;"
            " full copy stays local)"
        )

    body = (
        "## Environment\n\n"
        + env_block
        + "\n\n## Postmortem Analysis\n\n"
        + postmortem_section
        + "\n\n## Local Archive (not uploaded)\n\n"
        + refs_block
    )

    # Belt-and-braces only — with the allocation above a realistic
    # body never reaches this.
    if len(body) > MAX_BODY_CHARS:
        body = body[: MAX_BODY_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
    return body


__all__ = [
    "MAX_BODY_CHARS",
    "MAX_POSTMORTEM_CHARS",
    "redact",
    "should_publish_issue",
    "build_issue_title",
    "build_issue_body",
]
