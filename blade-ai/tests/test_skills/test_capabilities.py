"""Contract tests for skills/capabilities.py — the runtime registry reader.

Pinned behaviors (single source of truth, NO fallback fabrication):
1. Explicit path loads directly (test/CLI injection point).
2. Local sync output overrides the bundled registry ONLY when it is v2 AND
   its lang matches the requested locale (v1 files / other langs fall through).
3. Local override is staleness-checked against the skills-dir fingerprint.
4. Bundled registry serves when no applicable local file exists.
5. Both sources missing → CapabilitiesMissingError (never silent empty data).
6. Wrong schema_version → CapabilitiesError.
"""

from __future__ import annotations

import json

import pytest

from chaos_agent.skills import capabilities as caps


def _registry(lang: str, fingerprint: str = "", total: int = 1) -> dict:
    return {
        "schema_version": 2,
        "lang": lang,
        "generated_at": "2026-08-05T00:00:00+00:00",
        "skills_fingerprint": fingerprint,
        "total": total,
        "cases": [
            {
                "id": "host.host.cpu.fullload",
                "family": "host", "profile": "host",
                "scope": "host", "target": "cpu", "action": "fullload",
                "cluster_scoped": True,
                "title": "进程CPU满载" if lang == "zh" else "CPU fullload",
                "category": "Host_CPU使用率过高",
                "symptom": "s", "case_path": "references/catalogue/x.md",
                "nl_cmd": "blade-ai inject -i \"...\"",
                "params": [
                    {"name": "host", "kind": "target_resource", "resolved_from": "environment",
                     "required": True, "default": "", "description": "target host"},
                ],
                "executable": True,
            }
        ],
    }


def _write(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestExplicitPath:
    def test_load_from_explicit_path(self, tmp_path):
        p = tmp_path / "reg.json"
        _write(p, _registry("zh"))
        reg = caps.load_capabilities("zh", path=p)
        assert reg.total == 1
        case = reg.cases[0]
        assert case.id == "host.host.cpu.fullload"
        assert case.params[0].resolved_from == "environment"
        assert reg.families() == ["host"]
        assert reg.case_by_id("host.host.cpu.fullload") is case
        assert reg.case_by_id("nope") is None

    def test_wrong_schema_version_raises(self, tmp_path):
        p = tmp_path / "reg.json"
        data = _registry("zh")
        data["schema_version"] = 1
        _write(p, data)
        with pytest.raises(caps.CapabilitiesError):
            caps.load_capabilities("zh", path=p)


class TestResolutionOrder:
    def test_local_v2_matching_lang_overrides_bundled(self, tmp_path, monkeypatch):
        local = tmp_path / "local.json"
        bundled = tmp_path / "bundled.json"
        _write(local, _registry("zh"))
        _write(bundled, _registry("zh", total=2))
        monkeypatch.setattr(caps, "local_registry_path", lambda: local)
        monkeypatch.setattr(caps, "bundled_registry_path", lambda locale: bundled)

        reg = caps.load_capabilities("zh")
        assert reg.source == str(local)

    def test_local_other_lang_falls_through_to_bundled(self, tmp_path, monkeypatch):
        local = tmp_path / "local.json"
        bundled = tmp_path / "bundled.json"
        _write(local, _registry("zh"))          # 本地只有中文版
        _write(bundled, _registry("en"))
        monkeypatch.setattr(caps, "local_registry_path", lambda: local)
        monkeypatch.setattr(caps, "bundled_registry_path", lambda locale: bundled)

        reg = caps.load_capabilities("en")
        assert reg.source == str(bundled)

    def test_legacy_v1_local_is_ignored(self, tmp_path, monkeypatch):
        local = tmp_path / "local.json"
        bundled = tmp_path / "bundled.json"
        _write(local, {"generated_at": "x", "total": 1, "cases": []})  # v1 shape
        _write(bundled, _registry("zh"))
        monkeypatch.setattr(caps, "local_registry_path", lambda: local)
        monkeypatch.setattr(caps, "bundled_registry_path", lambda locale: bundled)

        reg = caps.load_capabilities("zh")
        assert reg.source == str(bundled)

    def test_both_missing_raises_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(caps, "local_registry_path", lambda: tmp_path / "nope.json")
        monkeypatch.setattr(caps, "bundled_registry_path",
                            lambda locale: tmp_path / "also_nope.json")
        with pytest.raises(caps.CapabilitiesMissingError):
            caps.load_capabilities("zh")


class TestStaleness:
    def test_stale_local_registry_raises(self, tmp_path, monkeypatch):
        local = tmp_path / "local.json"
        _write(local, _registry("zh", fingerprint="stale-fingerprint"))
        monkeypatch.setattr(caps, "local_registry_path", lambda: local)
        monkeypatch.setattr(caps, "compute_skills_fingerprint", lambda: "current-fingerprint")
        bundled = tmp_path / "bundled.json"
        monkeypatch.setattr(caps, "bundled_registry_path", lambda locale: bundled)

        with pytest.raises(caps.CapabilitiesStaleError):
            caps.load_capabilities("zh")

    def test_matching_fingerprint_passes(self, tmp_path, monkeypatch):
        local = tmp_path / "local.json"
        _write(local, _registry("zh", fingerprint="fp-1"))
        monkeypatch.setattr(caps, "local_registry_path", lambda: local)
        monkeypatch.setattr(caps, "compute_skills_fingerprint", lambda: "fp-1")

        reg = caps.load_capabilities("zh")
        assert reg.source == str(local)
