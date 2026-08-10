"""Contract tests for skills/case_sync.py — capabilities-sync generation (v2).

Pinned behaviors:
1. sync_capabilities derives commands from every fault-injection skill's
   catalogue (data-driven multi-skill; caller passes catalogue_roots).
2. Each case carries a ``skill`` field naming its source skill.
3. Parse failure → feedback retry (model's own answer is appended and it is
   asked to output JSON only; converges within the attempt budget).
4. Transport failure (LLM invoke raises) → terminal FAILED for that case,
   no case-layer retry (the LLM client owns the transport retry budget).
5. kubectl cases: direct_cmd stays empty (no blade --direct command), but the
   kubectl-native triple / structured_cmd are kept — the k8s_native provider
   injects by triple, so a complete triple means executable=true.
6. Output registry JSON: v2 envelope (schema_version/lang/generated_at/
   skills_fingerprint/total/cases), atomic write to disk.
7. Case naming is content-first: the md's own **用例名称** line wins over any
   filename convention; filename derivation is only the fallback.
8. v2 capability definition: stable id, family/profile/cluster_scoped
   derivation, executable flag, params normalization.
9. Cross-language alignment: passing ``reference_registry`` anchors the
   structural fields (triple/params) to the first-language artifact, so both
   languages stay id-isomorphic despite LLM derivation jitter; prose fields
   stay localized.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from chaos_agent.skills import case_sync


# ---------------------------------------------------------------------------
# Prompt contract
# ---------------------------------------------------------------------------

class TestPromptContract:
    """Wiring tests for the generation prompt's hard constraints."""

    def test_lang_parameter_defaults_to_english(self):
        """Language is caller-controlled; default must stay "en".

        Regression: a 2026-08 run produced 42 ZH / 48 EN nl_cmd entries
        because language was unconstrained. It is now pinned per run via
        --lang (default en, cn opt-in).
        """
        import inspect

        sig = inspect.signature(case_sync.sync_capabilities)
        assert sig.parameters["lang"].default == "en"
        assert set(case_sync._LANGUAGE_SECTIONS) == {"en", "cn"}

    def test_lang_cn_pins_chinese_and_en_pins_english(self):
        cn = case_sync._SYSTEM_PROMPT + case_sync._LANGUAGE_SECTIONS["cn"]
        en = case_sync._SYSTEM_PROMPT + case_sync._LANGUAGE_SECTIONS["en"]
        assert "简体中文" in cn and "简体中文" not in en
        assert "in English" in en
        for prompt in (cn, en):
            assert "fault_symptom" in prompt and "nl_cmd" in prompt


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, content: str):
        self.content = content
        self.additional_kwargs = {}


class FakeLLM:
    """Scripted LLM: pops one scripted response per ainvoke call."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)


def _make_catalogue(root: Path, category: str, cases: dict[str, str]) -> Path:
    cat_dir = root / "references" / "catalogue" / category
    cat_dir.mkdir(parents=True)
    for stem, body in cases.items():
        (cat_dir / f"{category}_{stem}.md").write_text(body, encoding="utf-8")
    return root / "references" / "catalogue"


def _blade_json(**overrides) -> str:
    payload = {
        "inject_kind": "blade",
        "title": "CPU fullload",
        "fault_symptom": "cpu high",
        "scope": "pod",
        "target": "cpu",
        "action": "fullload",
        "params": [
            {"name": "labels", "kind": "target_resource", "resolved_from": "environment",
             "required": True, "default": "", "description": "pod selector"},
            {"name": "cpu-percent", "kind": "fault_param", "resolved_from": "user",
             "required": False, "default": "80", "description": "cpu load percent"},
        ],
        "nl_cmd": 'blade-ai inject -i "stress cpu"',
        "structured_cmd": "blade-ai inject --scope pod --target cpu --action fullload",
        "direct_cmd": "blade-ai inject --direct --scope pod --target cpu --action fullload",
        "direct_hint": "",
    }
    payload.update(overrides)
    return json.dumps(payload)


def _run_sync(tmp_path: Path, llm, roots: dict[str, Path]) -> dict:
    out = tmp_path / "out.json"
    return asyncio.run(case_sync.sync_capabilities(roots, llm, out))


# ---------------------------------------------------------------------------
# Retry loop
# ---------------------------------------------------------------------------

class TestSyncRetryLoop:
    def test_parse_failure_feeds_back_and_retries(self, tmp_path):
        """Non-JSON answer → model gets its own answer + a 'JSON only' nudge
        appended, and the second answer is accepted."""
        good = _blade_json()
        llm = FakeLLM(["Here is some prose instead of JSON", good])
        roots = {"s1": _make_catalogue(tmp_path / "s1", "CPU", {"caseA": "# a"})}

        catalog = _run_sync(tmp_path, llm, roots)

        assert llm.calls and len(llm.calls) == 2
        assert catalog["total"] == 1
        assert catalog["cases"][0]["direct_cmd"] == json.loads(good)["direct_cmd"]
        # Second call carries the feedback pair
        second = llm.calls[1]
        assert second[-2].content.startswith("Here is some prose")
        assert "could not be parsed as JSON" in second[-1].content

    def test_transport_failure_is_terminal(self, tmp_path):
        """ConnectionResetError surfaces after the client's own retry budget —
        case-layer must not re-invoke; the case ends FAILED with a hint."""
        llm = FakeLLM([ConnectionResetError("boom")])
        roots = {"s1": _make_catalogue(tmp_path / "s1", "CPU", {"caseA": "# a"})}

        catalog = _run_sync(tmp_path, llm, roots)

        assert len(llm.calls) == 1
        case = catalog["cases"][0]
        assert case["inject_kind"] == "unknown"
        assert case["direct_cmd"] == ""
        assert "Re-run blade-ai capabilities-sync" in case["direct_hint"]
        # 无三元组的失败 case 不可执行（如实呈现，不伪装能力）
        assert case["executable"] is False


# ---------------------------------------------------------------------------
# kubectl semantics
# ---------------------------------------------------------------------------

class TestSyncKubectl:
    def test_kubectl_case_keeps_triple_forces_empty_direct(self, tmp_path):
        """Control-plane case: kubectl-native triple 与 structured_cmd 保留
        （k8s_native provider 按三元组注入/反向恢复），仅 direct_cmd 强制
        为空——无 blade --direct 命令不等于不可执行。"""
        payload = _blade_json(
            inject_kind="kubectl",
            scope="node",
            target="schedule",
            action="drain",
            structured_cmd="blade-ai inject --scope node --target schedule --action drain",
            direct_cmd="blade-ai inject --direct --scope node",
            direct_hint="Control-plane fault",
        )
        llm = FakeLLM([payload])
        roots = {"s1": _make_catalogue(tmp_path / "s1", "Node", {"caseA": "# a"})}

        catalog = _run_sync(tmp_path, llm, roots)

        case = catalog["cases"][0]
        assert case["inject_kind"] == "kubectl"
        assert case["direct_cmd"] == ""
        assert "--action drain" in case["structured_cmd"]
        assert case["direct_hint"] == "Control-plane fault"
        # 三元组齐备 → 可一键注入（kubectl-native 后端）
        assert case["executable"] is True
        assert (case["scope"], case["target"], case["action"]) == ("node", "schedule", "drain")

    def test_kubectl_case_without_triple_not_executable(self, tmp_path):
        """三元组缺失（模型推导失败）的 kubectl case 如实标为不可执行。"""
        payload = _blade_json(
            inject_kind="kubectl",
            scope="", target="", action="",
            structured_cmd="",
            direct_cmd="",
        )
        llm = FakeLLM([payload])
        roots = {"s1": _make_catalogue(tmp_path / "s1", "Node", {"caseA": "# a"})}

        catalog = _run_sync(tmp_path, llm, roots)

        case = catalog["cases"][0]
        assert case["executable"] is False
        assert case["direct_cmd"] == ""


# ---------------------------------------------------------------------------
# Case naming
# ---------------------------------------------------------------------------

class TestCaseNaming:
    def test_content_case_name_wins_over_filename(self, tmp_path):
        """A freely named file (no category prefix) whose content carries the
        **用例名称** line gets exactly that name — filenames are not forced."""
        cat_dir = tmp_path / "s1" / "references" / "catalogue" / "CPU"
        cat_dir.mkdir(parents=True)
        (cat_dir / "随便起名.md").write_text(
            "**用例名称** Sidecar容器CPU资源争抢 导致 CPU\n\n**故障现象**：\n1. x\n",
            encoding="utf-8",
        )
        llm = FakeLLM([_blade_json()])

        catalog = _run_sync(tmp_path, llm, {"s1": tmp_path / "s1" / "references" / "catalogue"})

        assert catalog["cases"][0]["use_case_name"] == "Sidecar容器CPU资源争抢 导致 CPU"

    def test_filename_fallback_when_content_has_no_name(self, tmp_path):
        """Legacy file without a **用例名称** line falls back to the
        {category}_{root_cause} filename derivation."""
        roots = {"s1": _make_catalogue(tmp_path / "s1", "CPU", {"caseA": "# no name line"})}
        llm = FakeLLM([_blade_json()])

        catalog = _run_sync(tmp_path, llm, roots)

        assert catalog["cases"][0]["use_case_name"] == "caseA 导致 CPU"


# ---------------------------------------------------------------------------
# Multi-skill merge
# ---------------------------------------------------------------------------

class TestSyncMultiSkill:
    def test_merges_all_catalogues_and_tags_skill(self, tmp_path):
        """Cases from every catalogue root are merged into one catalog and
        each record carries its source skill name."""
        roots = {
            "k8s-skill": _make_catalogue(
                tmp_path / "k8s", "CPU", {"caseK": "# k"}),
            "host-skill": _make_catalogue(
                tmp_path / "host", "MEM", {"caseH": "# h"}),
        }
        llm = FakeLLM([_blade_json(), _blade_json(inject_kind="blade")])

        out = tmp_path / "out.json"
        catalog = asyncio.run(case_sync.sync_capabilities(roots, llm, out))

        assert catalog["total"] == 2
        # v2 envelope：schema/lang/指纹/生成时间齐备，case 带稳定 id 与参数 schema
        assert catalog["schema_version"] == 2
        assert catalog["lang"] == "en"  # sync 默认英文（--lang cn 出 zh）
        assert "skills_fingerprint" in catalog and "generated_at" in catalog
        for c in catalog["cases"]:
            assert c["id"] and "family" in c and "profile" in c
            assert isinstance(c["params"], list)
        by_skill = {c["skill"]: c for c in catalog["cases"]}
        assert set(by_skill) == {"k8s-skill", "host-skill"}
        assert by_skill["k8s-skill"]["category"] == "CPU"
        assert by_skill["host-skill"]["category"] == "MEM"
        # 三元组齐备的 blade case 可执行；参数槽位归一化（环境槽位/用户参数）
        case = by_skill["k8s-skill"]
        assert case["executable"] is True
        assert (case["scope"], case["target"], case["action"]) == ("pod", "cpu", "fullload")
        kinds = {p["name"]: p["kind"] for p in case["params"]}
        assert kinds == {"labels": "target_resource", "cpu-percent": "fault_param"}
        # resource_path stays relative to the skill directory (existing contract)
        for c in catalog["cases"]:
            assert c["case_path"].startswith("references/catalogue/")
        # File was written atomically with the same content
        written = json.loads(out.read_text(encoding="utf-8"))
        assert written["total"] == 2
        assert written["schema_version"] == 2
        assert "blade_version" not in written

    def test_duplicate_triples_get_distinct_stable_ids(self, tmp_path):
        """同一三元组的多个 case（变体）获得后缀递增的稳定 id。"""
        roots = {
            "s1": _make_catalogue(tmp_path / "s1", "CPU", {"caseA": "# a", "caseB": "# b"}),
        }
        llm = FakeLLM([_blade_json(), _blade_json()])

        catalog = asyncio.run(case_sync.sync_capabilities(roots, llm, tmp_path / "out.json"))

        ids = [c["id"] for c in catalog["cases"]]
        assert len(set(ids)) == 2
        assert ids[0] == "k8s.pod.cpu.fullload"
        assert ids[1] == "k8s.pod.cpu.fullload.2"


# ---------------------------------------------------------------------------
# Cross-language alignment
# ---------------------------------------------------------------------------

class TestCrossLanguageAlignment:
    """双语产物必须 id 同构：第二语言生成时用 --reference 锚定结构字段。

    回归：2026-08 首次双语生成时 LLM 三元组推导漂移，27/90 个 case 的
    id 在 zh/en 间不一致，平台按 id 合并双语文本失效。
    """

    def test_reference_anchors_structure_and_keeps_ids_isomorphic(self, tmp_path):
        # 第一语言（zh）：正常生成
        roots = {"s1": _make_catalogue(tmp_path / "s1", "CPU", {"caseA": "# a"})}
        zh_catalog = asyncio.run(case_sync.sync_capabilities(
            roots, FakeLLM([_blade_json(title="CPU满载", fault_symptom="CPU 过高")]),
            tmp_path / "zh.json", lang="cn",
        ))

        # 第二语言（en）：LLM 漂移出了不同三元组/参数，但传了 reference
        drifted = _blade_json(
            title="CPU Fullload", fault_symptom="cpu high",
            target="process", action="kill",  # 漂移的三元组
            params=[{"name": "cpu-percent", "kind": "fault_param", "resolved_from": "user",
                     "required": False, "default": "90", "description": "cpu load percent"}],
        )
        en_catalog = asyncio.run(case_sync.sync_capabilities(
            roots, FakeLLM([drifted]), tmp_path / "en.json", lang="en",
            reference_registry=tmp_path / "zh.json",
        ))

        zh_case, en_case = zh_catalog["cases"][0], en_catalog["cases"][0]
        # id 完全一致（结构锚定后派生结果相同）
        assert en_case["id"] == zh_case["id"]
        # 三元组/槽位结构锚定到参考，不被漂移污染
        assert (en_case["scope"], en_case["target"], en_case["action"]) == ("pod", "cpu", "fullload")
        names = [p["name"] for p in en_case["params"]]
        assert names == [p["name"] for p in zh_case["params"]]
        # 同名槽位保留英文 description，缺失槽位回退参考文本
        by_name = {p["name"]: p for p in en_case["params"]}
        assert by_name["cpu-percent"]["description"] == "cpu load percent"
        assert by_name["labels"]["description"] == "pod selector"
        # prose 字段保持本次语言生成结果
        assert en_case["title"] == "CPU Fullload"
        assert en_case["symptom"] == "cpu high"

    def test_unreadable_reference_is_a_noop(self, tmp_path):
        """参考文件不可读 → 告警跳过对齐，不阻断生成。"""
        roots = {"s1": _make_catalogue(tmp_path / "s1", "CPU", {"caseA": "# a"})}
        catalog = asyncio.run(case_sync.sync_capabilities(
            roots, FakeLLM([_blade_json()]), tmp_path / "en.json",
            reference_registry=tmp_path / "missing.json",
        ))
        assert catalog["total"] == 1
        assert catalog["cases"][0]["target"] == "cpu"
