"""CLI command: blade-ai web

Launch the Web UI: an embedded FastAPI server (loopback + OS-allocated
port, token gate bypassed) hosting ``web/dist``, plus a browser tab.

Local single-machine form ONLY — this command takes no ``--host`` and
always binds 127.0.0.1. The embedded server bypasses token auth (same
contract as the TUI's ``__embedded_server__``), so allowing a non-
loopback bind would expose an unauthenticated fault-injection API to
the LAN. Remote hosting is ``blade-ai server`` with a configured
``server_token`` (see docs/design §4.2, phase P4).
"""

import socket
import threading
import time
import urllib.request
import webbrowser

import typer

_HEALTH_TIMEOUT_S = 15.0


def _allocate_loopback_port() -> int:
    """Ask the OS for a free loopback port (closes before uvicorn rebinds)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _open_browser_when_ready(url: str) -> None:
    """Open the browser once the server answers /health.

    The lifespan phase (skill loading, LLM creation) can take seconds
    on a cold start; opening the tab immediately would race it and show
    the user a connection error. Poll instead, and open anyway on
    timeout so a genuinely broken server surfaces as a browser error
    rather than silence.
    """
    deadline = time.monotonic() + _HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"{url}/api/v1/health", timeout=1,
            ) as resp:
                if resp.status == 200:
                    webbrowser.open(url)
                    return
        except Exception:
            time.sleep(0.2)
    webbrowser.open(url)


def web_command(
    port: int = typer.Option(
        0, "--port", "-p",
        help="Loopback port; 0 lets the OS allocate one (default).",
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser",
        help="Don't open a browser tab (headless / SSH sessions).",
    ),
) -> None:
    """Start the Web UI locally and open it in a browser."""
    from chaos_agent.server.app import run_server
    from chaos_agent.server.web import resolve_web_dist

    if resolve_web_dist() is None:
        typer.secho(
            "blade-ai web: the Web UI bundle (web/dist) was not found.\n"
            "  build from source: npm --prefix web run build\n"
            "  or install a blade-ai wheel/binary that embeds it.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    resolved_port = port if port != 0 else _allocate_loopback_port()
    url = f"http://127.0.0.1:{resolved_port}"

    if not no_browser:
        threading.Thread(
            target=_open_browser_when_ready, args=(url,), daemon=True,
        ).start()

    typer.echo(f"blade-ai web: serving the Web UI at {url}  (Ctrl+C to stop)")

    # embedded=True — same contract as the TUI's __embedded_server__:
    # loopback + per-launch port, so the token gate is bypassed (the
    # browser sends no Authorization header; a configured token would
    # 401-block the whole UI without adding protection on loopback).
    run_server(host="127.0.0.1", port=resolved_port, embedded=True)
