"""CLI command: blade-ai config - Manage unified configuration."""

import typer

from chaos_agent.cli.config_manager import (
    SENSITIVE_KEYS,
    get_config,
    list_config,
    set_config,
    LOCAL,
    SERVER,
)
from chaos_agent.cli.output import OutputFormat, format_output


def _mask_value(key: str, value: object) -> object:
    """Mask sensitive values for display."""
    if key in SENSITIVE_KEYS and isinstance(value, str) and len(value) > 4:
        return value[:4] + "*" * (len(value) - 4)
    return value


def _parse_value(key: str, raw: str) -> object:
    """Parse a string value into the type Settings consumes for *key*.

    Delegates to ``ConfigStore._coerce`` — the same single source of
    truth the server's ``/config`` write path and the Python TUI's
    ``/config set`` use (bool/int/float key sets live there). Keeping
    a second, smaller key list here previously left typed keys like
    ``max_verifier_loop`` unprotected: an unparsed string would poison
    config.json — the HIGHEST-priority settings source — making
    ``Settings()`` construction fail for every command afterwards, with
    the traceback pointing at pydantic instead of the ``config set``
    that wrote it.

    Raises ValueError when *raw* cannot be converted.
    """
    from chaos_agent.config.config_store import ConfigStore

    return ConfigStore._coerce(key, raw)


def config_command(
    action: str = typer.Argument(
        "list",
        help="Action: 'list' | 'get' | 'set'",
    ),
    key: str = typer.Argument(
        None,
        help="Config key name (required for get/set)",
    ),
    value: str = typer.Argument(
        None,
        help="Config value (required for set)",
    ),
    extra: str = typer.Argument(
        None,
        help="Extra value (used when key=mode and value=server, this is the server URL)",
    ),
    output: OutputFormat = typer.Option(OutputFormat.json, "--output", "-o", help="Output format: json|yaml"),
):
    """Manage configuration: view and set all config values.

    Examples:
      blade-ai config                        # List all config
      blade-ai config list                   # List all config
      blade-ai config get mode               # Get a single config value
      blade-ai config get llm_api_key        # Get API key (masked)
      blade-ai config set mode local         # Switch to local mode
      blade-ai config set mode server http://host:8089  # Switch to server mode
      blade-ai config set llm_api_key sk-xxx # Set API key
      blade-ai config set model_name glm-5.1 # Set model name
    """
    if action == "list":
        data = list_config()
        masked = {k: _mask_value(k, v) for k, v in data.items()}
        result = {"code": 0, "message": "success", "data": masked}

    elif action == "get":
        if not key:
            result = {"code": 1001, "message": "Key is required for 'get'. Usage: blade-ai config get <key>", "data": None}
        else:
            val = get_config(key)
            if val is None:
                result = {"code": 1002, "message": f"Unknown config key: {key}", "data": None}
            else:
                result = {"code": 0, "message": "success", "data": {key: _mask_value(key, val)}}

    elif action == "set":
        if not key:
            result = {"code": 1001, "message": "Key is required for 'set'. Usage: blade-ai config set <key> <value>", "data": None}
        elif key == "mode":
            # Special handling for mode: set mode local | set mode server <url>
            mode_val = value or LOCAL
            if mode_val == LOCAL:
                set_config("mode", LOCAL)
                set_config("server_url", None)
                result = {"code": 0, "message": "success", "data": {"mode": LOCAL, "server_url": None}}
            elif mode_val == SERVER:
                if not extra:
                    result = {"code": 1001, "message": "Server URL is required. Usage: blade-ai config set mode server <url>", "data": None}
                else:
                    set_config("mode", SERVER)
                    set_config("server_url", extra.rstrip("/"))
                    result = {"code": 0, "message": "success", "data": {"mode": SERVER, "server_url": extra.rstrip("/")}}
            else:
                result = {"code": 1001, "message": f"Unknown mode '{mode_val}'. Use: local | server", "data": None}
        else:
            if value is None:
                parsed = value
            else:
                try:
                    parsed = _parse_value(key, value)
                except ValueError as e:
                    result = {"code": 1001, "message": str(e), "data": None}
                    typer.echo(format_output(result, output))
                    return
            set_config(key, parsed)
            result = {"code": 0, "message": "success", "data": {key: _mask_value(key, parsed)}}

    else:
        result = {"code": 1001, "message": f"Unknown action '{action}'. Use: list | get | set", "data": None}

    typer.echo(format_output(result, output))
