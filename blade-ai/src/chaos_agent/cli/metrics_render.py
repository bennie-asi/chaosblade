"""Human-readable rendering for ``blade-ai metric`` (text output format).

Pure ``data → str`` transforms over the ``get_metric`` / ``get_all_metrics``
envelopes (task_store.py). No I/O, no ANSI baked in — callers pass
``color=True`` only when stdout is a TTY. Mirrors the TS TUI's
``formatReviewCard`` philosophy but renders the FULL envelope, including
the node-level ``spans`` waterfall that no other runtime consumer shows.
"""

from __future__ import annotations

import re
import sys
from io import StringIO

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text


# ---------------------------------------------------------------------------
# ANSI colors — applied only when *color* is True (caller checks isatty)
# ---------------------------------------------------------------------------

_CODES = {
    "green": "\033[32m",
    "red": "\033[31m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def _c(text: str, name: str, color: bool) -> str:
    if not color:
        return text
    return f"{_CODES[name]}{text}{_CODES['reset']}"


_STATUS_GLYPHS = {
    "success": ("✓", "green"),
    "failed": ("✗", "red"),
    "in_progress": ("●", "yellow"),
    "pending": ("○", "yellow"),
}

_BAR_WIDTH = 34
_LABEL_WIDTH = 22

# Trailing whitespace, possibly interleaved with ANSI escapes, at
# end-of-line: rich pads the final column even with pad_edge=False.
_TRAILING_PAD = re.compile(r"(?:\s|\033\[[0-9;]*m)+$")


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------

def format_ms(ms: int | float) -> str:
    """675517 → '11m15s'; 4800 → '4.8s'; 350 → '350ms'."""
    ms = int(ms or 0)
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _short_id(task_id: str) -> str:
    """inject-3198b391-b2ab-4ed4-b91b-1a3941209c51 → inject-3198b391."""
    parts = task_id.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return task_id[:16]


def _short_ts(iso: str) -> str:
    """2026-08-13T16:32:02.801372+08:00 → 2026-08-13 16:32."""
    return (iso or "").replace("T", " ")[:16]


def _format_target(target, params) -> str:
    """Render the target dict + params into one readable line."""
    if not isinstance(target, dict):
        return "—"
    ns = target.get("namespace") or "?"
    names = target.get("names") or []
    labels = target.get("labels") or {}
    rtype = target.get("resource_type") or ""
    if names:
        shown = ", ".join(str(n) for n in names[:3])
        if len(names) > 3:
            shown += f" +{len(names) - 3} more"
        ident = shown
    elif labels:
        ident = "labels " + ",".join(f"{k}={v}" for k, v in labels.items())
    else:
        ident = "?"
    line = f"{ns}/{ident}"
    extras = [rtype] if rtype else []
    if isinstance(params, dict) and params:
        extras += [f"{k}={v}" for k, v in list(params.items())[:4]]
    if extras:
        line += f" ({', '.join(str(e) for e in extras)})"
    return line


# ---------------------------------------------------------------------------
# Single-task view (get_metric envelope)
# ---------------------------------------------------------------------------

def render_metric(data: dict, color: bool = False) -> str:
    """Render one task's full metric envelope as a readable report."""
    lines: list[str] = []
    lines += _render_header(data, color)
    lines.append("")
    lines += _render_verification(data, color)
    lines += _render_spans(data.get("spans") or [], color)
    error = (data.get("error") or "").strip()
    if error:
        lines.append("")
        lines.append(_c("Error", "red", color))
        for chunk in error.splitlines() or [error]:
            lines.append(f"  {chunk}")
    return "\n".join(lines)


def _render_header(data: dict, color: bool) -> list[str]:
    status = (data.get("status") or "?").lower()
    glyph, col = _STATUS_GLYPHS.get(status, ("?", "yellow"))
    title = f"{_c(glyph, col, color)} {_c(_short_id(data.get('task_id', '')), 'bold', color)}  "
    title += f"{_c(status, col, color)}"
    fault = data.get("fault_type") or data.get("skill_name") or ""
    if fault:
        title += f" · {fault}"
    lines = [title]

    rows = [
        ("target", _format_target(data.get("target"), data.get("params"))),
        ("phase", f"{data.get('phase') or '—'}  ·  safety {data.get('safety_status') or '?'}"),
    ]
    created = _short_ts(data.get("gmt_create", ""))
    finished = _short_ts(data.get("finished_at", ""))
    window = created or "—"
    if finished:
        window += f" → {finished}"
    duration = data.get("duration_ms") or 0
    if duration:
        window += f"  ({format_ms(duration)})"
    rows.append(("window", window))

    # LLM model frozen at task finalize — empty for tasks archived before
    # the model_name column existed, so render only when present (same
    # discipline as experiment_uid below).
    if data.get("model_name"):
        rows.append(("model", str(data["model_name"])))

    summary = data.get("summary") or {}
    tok_in = summary.get("total_token_input") or 0
    tok_out = summary.get("total_token_output") or 0
    llm = summary.get("total_llm_calls") or 0
    tools = summary.get("total_tool_calls") or 0
    if tok_in or tok_out or llm or tools:
        rows.append(("cost", f"{tok_in}↓ {tok_out}↑ tokens · LLM ×{llm} · tools ×{tools}"))
    experiment_uid = data.get("experiment_uid")
    if experiment_uid:
        rows.append(("experiment uid", str(experiment_uid)))

    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        lines.append(f"  {k.ljust(width)}  {v}")
    return lines


def _render_verification(data: dict, color: bool) -> list[str]:
    verif = data.get("verification")
    if not isinstance(verif, dict):
        return []
    lines = [_c("Verification", "cyan", color)]
    for key, label in (("layer1", "L1"), ("layer2", "L2")):
        layer = verif.get(key)
        if not isinstance(layer, dict):
            continue
        st = (layer.get("status") or "?").lower()
        col = {"passed": "green", "failed": "red", "skipped": "yellow"}.get(st, "yellow")
        head = f"  {label} {_c(st, col, color)}"
        details = (layer.get("details") or "").strip()
        if details:
            head += f" — {_first_sentence(details)}"
        lines.append(head)
    warnings = verif.get("warnings") or []
    if warnings:
        lines.append(f"  {_c('⚠', 'yellow', color)} {len(warnings)} warning(s)")
        for w in warnings:
            lines.append(f"    - {_first_sentence(str(w).strip())}")
    return lines


def _first_sentence(text: str, limit: int = 140) -> str:
    """First sentence (or hard cut) so long LLM verdicts stay one line."""
    for sep in ("。", ". "):
        idx = text.find(sep)
        if 0 < idx < limit:
            return text[: idx + len(sep)].rstrip()
    return text[:limit] + ("…" if len(text) > limit else "")


# ---------------------------------------------------------------------------
# Node span waterfall
# ---------------------------------------------------------------------------

def _render_spans(spans: list[dict], color: bool) -> list[str]:
    if not spans:
        return ["", _c("Node Spans", "cyan", color), "  (none recorded)"]

    ordered = sorted(spans, key=lambda s: s.get("start_time") or 0.0)
    t0 = ordered[0].get("start_time") or 0.0
    total = max(
        (s.get("start_time") or 0.0) - t0 for s in ordered
    ) * 1000 + (ordered[-1].get("duration_ms") or 0.0)
    total = max(total, 1.0)

    lines = [
        "",
        f"{_c('Node Spans', 'cyan', color)} ({len(ordered)})  ·  span window {format_ms(total)}",
    ]
    for s in ordered:
        offset_ms = ((s.get("start_time") or 0.0) - t0) * 1000
        dur = s.get("duration_ms") or 0.0
        left = int(offset_ms / total * _BAR_WIDTH)
        width = max(1, int(dur / total * _BAR_WIDTH))
        width = min(width, _BAR_WIDTH - left)

        error = s.get("error") or ""
        interrupted = "interrupt" in error
        if interrupted:
            bar = _c("┄" * max(width, 1), "dim", color)
        elif error:
            bar = _c("█" * width, "red", color)
        else:
            bar = _c("█" * width, "green", color)
        # pad by VISIBLE width — bar may carry ANSI escapes
        track = " " * left + bar + " " * max(0, _BAR_WIDTH - left - width)

        meta = [format_ms(dur)]
        tok_in = s.get("token_input") or 0
        tok_out = s.get("token_output") or 0
        if tok_in or tok_out:
            meta.append(f"{tok_in}↓{tok_out}↑")
        tools = s.get("tool_calls") or []
        if tools:
            counts: dict[str, int] = {}
            for t in tools:
                counts[t] = counts.get(t, 0) + 1
            meta.append(" ".join(f"{name}×{n}" for name, n in counts.items()))
        if error:
            meta.append(_c("⏸" if interrupted else "✗", "yellow" if interrupted else "red", color))

        label = (s.get("node_name") or "?")[:_LABEL_WIDTH].ljust(_LABEL_WIDTH)
        lines.append(f"  {label} {format_ms(offset_ms):>8}  {track}  {' · '.join(meta)}")
    return lines


# ---------------------------------------------------------------------------
# All-tasks list view (get_all_metrics envelope)
# ---------------------------------------------------------------------------

def render_metric_list(data: dict, color: bool = False) -> str:
    """Render the all-tasks envelope as a rich Table."""
    tasks = data.get("tasks") or []
    total = data.get("total", len(tasks))
    if not tasks:
        return "No tasks recorded yet."

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("", width=2)  # status glyph
    table.add_column("TASK", no_wrap=True)
    table.add_column("STATUS")
    table.add_column("FAULT")
    table.add_column("PHASE")
    table.add_column("DUR", justify="right")
    table.add_column("CREATED", no_wrap=True)

    for t in tasks:
        status = (t.get("status") or "?").lower()
        glyph, col = _STATUS_GLYPHS.get(status, ("?", "yellow"))
        dur = (t.get("summary") or {}).get("total_duration_ms") or 0
        table.add_row(
            Text(glyph, style=col),
            _short_id(t.get("task_id", "")),
            Text(status, style=col),
            (t.get("fault_type") or t.get("skill_name") or "—")[:24],
            (t.get("phase") or "—")[:16],
            format_ms(dur) if dur else "—",
            _short_ts(t.get("gmt_create", "")),
        )

    buf = StringIO()
    # force_terminal controls ANSI emission: plain text when piped.
    console = Console(file=buf, width=200, force_terminal=color, no_color=not color)
    console.print(table)
    # rich pads rows to the table's natural width — strip the trailing
    # padding (which in color mode sits before a final ANSI reset) and any
    # blank margin lines so the output hugs the content.
    lines = [_TRAILING_PAD.sub("", line).rstrip()
             for line in buf.getvalue().splitlines()]
    lines = [line for line in lines if line.strip()]
    return f"Tasks: {total}\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point used by the metric command
# ---------------------------------------------------------------------------

def render_text(data: dict) -> str:
    """Dispatch on envelope shape and render for the current stdout."""
    color = sys.stdout.isatty()
    if "tasks" in data:  # get_all_metrics shape
        return render_metric_list(data, color=color)
    return render_metric(data, color=color)
