"""Tests for CLI commands."""

from chaos_agent.cli.output import format_output


class TestConfigCommand:
    def test_show_current_mode(self, tmp_mode_dir):
        """config_command list should show current mode."""
        from chaos_agent.cli.config_manager import set_config
        set_config("mode", "local")

        # Test the underlying config_manager functions
        from chaos_agent.cli.config_manager import get_config
        result = get_config("mode")
        assert result == "local"

    def test_set_local_mode(self, tmp_mode_dir):
        from chaos_agent.cli.config_manager import set_config, get_mode
        set_config("mode", "local")
        assert get_mode() == "local"

    def test_set_server_mode(self, tmp_mode_dir):
        from chaos_agent.cli.config_manager import set_config, get_mode, get_server_url
        set_config("mode", "server")
        set_config("server_url", "http://localhost:8089")
        assert get_mode() == "server"
        assert get_server_url() == "http://localhost:8089"

    def test_config_list_result_structure(self, tmp_mode_dir):
        from chaos_agent.cli.config_manager import list_config
        result = list_config()
        assert "mode" in result
        assert "server_url" in result


class TestConfigValueParsing:
    """config set must refuse wrong-typed values instead of storing them.

    config.json is the highest-priority settings source: a string stored
    for an int/float/bool key makes Settings() construction raise for
    every subsequent command.
    """

    def test_parse_int_valid(self):
        from chaos_agent.cli.commands.config import _parse_value
        assert _parse_value("server_port", "9090") == 9090

    def test_parse_int_invalid_raises(self):
        import pytest
        from chaos_agent.cli.commands.config import _parse_value
        with pytest.raises(ValueError):
            _parse_value("server_port", "abc")

    def test_parse_float_invalid_raises(self):
        import pytest
        from chaos_agent.cli.commands.config import _parse_value
        with pytest.raises(ValueError):
            _parse_value("llm_temperature", "warm")

    def test_parse_bool_valid(self):
        from chaos_agent.cli.commands.config import _parse_value
        assert _parse_value("confirmation_required", "true") is True
        assert _parse_value("confirmation_required", "False") is False

    def test_parse_bool_invalid_raises(self):
        import pytest
        from chaos_agent.cli.commands.config import _parse_value
        with pytest.raises(ValueError):
            _parse_value("confirmation_required", "maybe")

    def test_parse_delegates_to_config_store_key_sets(self):
        """Regression: parsing must cover EVERY typed key ConfigStore
        knows about, not a second hand-maintained list. These keys were
        previously outside the CLI's private whitelist and would have
        been stored as raw strings (the exact config-poisoning bug the
        validation was meant to kill)."""
        import pytest
        from chaos_agent.cli.commands.config import _parse_value
        # int keys absent from the old CLI list
        assert _parse_value("max_verifier_loop", "5") == 5
        assert _parse_value("loop_detection_window", "3") == 3
        with pytest.raises(ValueError):
            _parse_value("max_verifier_loop", "abc")
        # bool keys absent from the old CLI list
        assert _parse_value("self_evolution", "true") is True
        with pytest.raises(ValueError):
            _parse_value("otel_enabled", "maybe")
        # float keys absent from the old CLI list
        assert _parse_value("context_compact_ratio", "0.6") == 0.6
        with pytest.raises(ValueError):
            _parse_value("context_compact_ratio", "lots")

    def test_set_rejects_bad_int_without_writing(self, tmp_mode_dir, capsys):
        import json
        from chaos_agent.cli.commands.config import config_command
        from chaos_agent.cli.config_manager import get_config
        from chaos_agent.cli.output import OutputFormat

        # Bypassing typer's CLI runner means Option defaults don't apply;
        # pass every argument explicitly.
        config_command(
            action="set", key="server_port", value="abc", extra=None,
            output=OutputFormat.json,
        )
        envelope = json.loads(capsys.readouterr().out)
        assert envelope["code"] == 1001
        assert "not a valid integer" in envelope["message"]
        # The bad value must NOT have been persisted.
        assert get_config("server_port") == 8089


class TestInjectCommandParsing:
    """Test inject command parameter parsing logic."""

    def test_params_parsing(self):
        """Test key=value params string parsing."""
        params_str = "latency=100,jitter=true"
        params_dict = {}
        for pair in params_str.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params_dict[k.strip()] = v.strip()

        assert params_dict == {"latency": "100", "jitter": "true"}

    def test_params_parsing_with_spaces(self):
        params_str = "latency = 100 , jitter = true"
        params_dict = {}
        for pair in params_str.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params_dict[k.strip()] = v.strip()

        assert params_dict == {"latency": "100", "jitter": "true"}

    def test_params_empty(self):
        params_dict = {}
        # No params string provided
        assert params_dict == {}

    def test_labels_parsing(self):
        labels_str = "env=test,team=chaos"
        labels_dict = {}
        for pair in labels_str.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                labels_dict[k.strip()] = v.strip()

        assert labels_dict == {"env": "test", "team": "chaos"}


class TestOutputFormatting:
    """Test output format integration with commands."""

    def test_format_mode_result(self):
        result = {"code": 0, "message": "success", "data": {"mode": "local"}}
        formatted = format_output(result, "json")
        import json
        parsed = json.loads(formatted)
        assert parsed["data"]["mode"] == "local"

    def test_format_error_result(self):
        result = {"code": 1001, "message": "Invalid action", "data": None}
        formatted = format_output(result, "json")
        import json
        parsed = json.loads(formatted)
        assert parsed["code"] == 1001
