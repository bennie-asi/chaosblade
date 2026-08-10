"""Drill-failure GitHub issue reporting subsystem.

When an inject task FINALIZES AS FAILED (existing save_memory failure
criterion — this module adds no new failure semantics), the postmortem
markdown plus a redacted execution-record summary are published as an
issue on the configured GitHub repository (default
``chaosblade-io/chaosblade``) for community fault-data collection.

Fire-and-forget: hard timeout, zero retries, every exception contained
— the drill's result output is never blocked by this step. Missing
token degrades to a local archive plus a TUI hint (no publish).

Public surface:
    should_publish_issue(state, settings) → bool
    publish_issue_report(state, task_id, postmortem_payload) → dict
    redact(text) → str
    get_issue_report_dir() → Path
"""

from chaos_agent.agent.issue_report.builder import (
    MAX_BODY_CHARS,
    build_issue_body,
    build_issue_title,
    redact,
    should_publish_issue,
)
from chaos_agent.agent.issue_report.publisher import (
    get_issue_report_dir,
    publish_issue_report,
)

__all__ = [
    "MAX_BODY_CHARS",
    "build_issue_body",
    "build_issue_title",
    "redact",
    "should_publish_issue",
    "get_issue_report_dir",
    "publish_issue_report",
]
