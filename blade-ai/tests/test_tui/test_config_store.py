"""Tests for ConfigStore."""

import json
import os

import pytest

from chaos_agent.config.config_store import ConfigStore


@pytest.fixture
def config_dir(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "llm_api_key": "sk-test-1234567890abcdef",
        "model_name": "qwen3.6-max-preview",
        "confirmation_required": True,
        "llm_max_retries": 3,
    }))
    return str(tmp_path)


class TestConfigStoreRead:
    def test_read_all(self, config_dir):
        store = ConfigStore(os.path.join(config_dir, "config.json"))
        data = store.read_all()
        assert data["model_name"] == "qwen3.6-max-preview"
        assert data["llm_api_key"] == "sk-test-1234567890abcdef"

    def test_read_nonexistent(self, tmp_path):
        store = ConfigStore(str(tmp_path / "missing.json"))
        data = store.read_all()
        assert data == {}

    def test_read_corrupted_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json")
        store = ConfigStore(str(bad))
        assert store.read_all() == {}


class TestConfigStoreWrite:
    def test_set_single_key(self, config_dir):
        store = ConfigStore(os.path.join(config_dir, "config.json"))
        store.set("model_name", "deepseek-chat")
        data = store.read_all()
        assert data["model_name"] == "deepseek-chat"

    def test_set_bool_coercion(self, config_dir):
        store = ConfigStore(os.path.join(config_dir, "config.json"))
        store.set("confirmation_required", "false")
        data = store.read_all()
        assert data["confirmation_required"] is False

    def test_set_int_coercion(self, config_dir):
        store = ConfigStore(os.path.join(config_dir, "config.json"))
        store.set("llm_max_retries", "5")
        data = store.read_all()
        assert data["llm_max_retries"] == 5

    def test_set_many(self, config_dir):
        store = ConfigStore(os.path.join(config_dir, "config.json"))
        store.set_many({"model_name": "test-model", "llm_max_retries": 10})
        data = store.read_all()
        assert data["model_name"] == "test-model"
        assert data["llm_max_retries"] == 10

    def test_atomic_write(self, config_dir):
        p = os.path.join(config_dir, "config.json")
        store = ConfigStore(p)
        store.set("model_name", "atomic-test")
        assert not os.path.exists(p + ".tmp")
        data = store.read_all()
        assert data["model_name"] == "atomic-test"


class TestCoercionRejectsWrongTypes:
    """_coerce must RAISE on unparseable values instead of silently
    casting (old bool behaviour: any string → False) or storing raw
    strings. A wrong-typed value in config.json — the highest-priority
    settings source — breaks Settings() construction for every later
    command. All write paths (server /config, wizard save, Python TUI
    /config set, CLI config set) already handle ValueError."""

    def test_bool_rejects_garbage(self, config_dir):
        store = ConfigStore(os.path.join(config_dir, "config.json"))
        with pytest.raises(ValueError, match="not a valid boolean"):
            store.set("confirmation_required", "maybe")
        # The rejected write must not have landed.
        assert store.read_all()["confirmation_required"] is True

    def test_bool_accepts_common_spellings(self, config_dir):
        store = ConfigStore(os.path.join(config_dir, "config.json"))
        for raw, expected in [("on", True), ("off", False), ("1", True), ("no", False)]:
            store.set("confirmation_required", raw)
            assert store.read_all()["confirmation_required"] is expected

    def test_int_rejects_garbage(self, config_dir):
        store = ConfigStore(os.path.join(config_dir, "config.json"))
        with pytest.raises(ValueError, match="not a valid integer"):
            store.set("llm_max_retries", "lots")
        assert store.read_all()["llm_max_retries"] == 3

    def test_float_rejects_garbage(self, config_dir):
        store = ConfigStore(os.path.join(config_dir, "config.json"))
        with pytest.raises(ValueError, match="not a valid number"):
            store.set("llm_temperature", "warm")

    def test_introspection_covers_fields_outside_key_sets(self, config_dir):
        """Typed Settings fields that no curated set mentions (e.g.
        ``otel_enabled`` / ``postmortem_timeout_seconds``) must still
        coerce — the Settings.model_fields fallback is what keeps the
        guard complete as settings grow."""
        store = ConfigStore(os.path.join(config_dir, "config.json"))
        store.set("otel_enabled", "true")
        assert store.read_all()["otel_enabled"] is True
        with pytest.raises(ValueError, match="not a valid boolean"):
            store.set("otel_enabled", "maybe")
        store.set("postmortem_timeout_seconds", "120")
        assert store.read_all()["postmortem_timeout_seconds"] == 120
