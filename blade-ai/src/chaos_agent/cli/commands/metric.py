"""CLI command: blade-ai metric"""

import asyncio

import typer

from chaos_agent.cli.config_manager import get_backend
from chaos_agent.cli.metrics_render import render_text
from chaos_agent.cli.output import format_output
from chaos_agent.preflight import exit_for_envelope

# ``text`` is metric-specific human-readable rendering (overview card +
# verification + node-span waterfall); json/yaml stay machine-readable.
_MetricFormat = typer.Option(
    "text",
    "--output", "-o",
    help="Output format: text (human) | json | yaml",
)


def metric_command(
    task_id: str = typer.Option("", "--task-id", help="Task ID (omit to list all tasks)"),
    output: str = _MetricFormat,
):
    """Query task status and execution metrics.

    Without --task-id, lists ALL tasks with status and metrics summary.

    With --task-id, shows a readable report: status/fault/target header,
    verification verdicts, and the node-span waterfall (per-node timing,
    tokens and tool calls). Use -o json|-o yaml for machine consumption.
    """
    backend = get_backend()

    async def _run():
        try:
            return await backend.metric(task_id)
        finally:
            await backend.cleanup()

    result = asyncio.run(_run())
    if output == "text":
        data = result.get("data") if isinstance(result, dict) else None
        if result.get("code") in (0, None) and isinstance(data, dict):
            typer.echo(render_text(data))
        elif result.get("code") in (0, None):
            # Success envelope with an unexpected shape — fall back to
            # raw JSON rather than misreporting as a failure.
            typer.echo(format_output(result, "json"))
        else:
            # Failed envelope (e.g. task not found) — surface the message
            typer.echo(f"✗ {result.get('message') or 'request failed'}")
    else:
        typer.echo(format_output(result, output))
    exit_for_envelope(result)
