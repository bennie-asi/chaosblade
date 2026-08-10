"""Sync skill capabilities: LLM distills each catalogue case into an
executable capability definition (v2).

`sync_capabilities` is the main entry point called by `blade-ai capabilities-sync`.
It asks an LLM to derive, directly from each skill case's markdown — the case
library is authoritative, no live probing of the blade binary is performed:

- the NL command (``nl_cmd``) and the classic structured/direct command
  variants (kept for ``blade-ai list`` and other existing consumers)
- the machine-consumable capability definition: scope/target/action triple,
  a typed parameter schema (which slot comes from the environment config vs.
  which one the operator fills) and an executability flag

The output is a v2 JSON registry written to
``~/.blade-ai/memory/skill_capabilities.json``. Package maintainers commit the
same artifact (per language) into ``chaos_agent/_capabilities/`` so installing
the package ships its capabilities; ``chaos_agent.skills.capabilities`` is the
runtime reader and the single consumption entry point.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from chaos_agent.skills.capabilities import SCHEMA_VERSION

logger = logging.getLogger(__name__)

# Structural fields anchored to the reference (first-language) registry when
# generating a second language. LLM triple/param derivation has run-to-run
# jitter; without anchoring the two language artifacts drift apart (different
# ids) and bilingual consumers can no longer merge cases by id. Prose fields
# (title/symptom/nl_cmd/direct_hint) stay localized.
_STRUCTURAL_FIELDS = (
    "inject_kind", "scope", "target", "action", "params", "structured_cmd", "direct_cmd",
)
_PROSE_FIELDS = ("title", "fault_symptom", "nl_cmd", "direct_hint")

# Authoritative case name lives in the md content (``**用例名称** ...``),
# same line extract_planning_metadata consumes. Filename conventions are only
# a fallback for legacy files that lack the line.
_CASE_NAME_RE = re.compile(r"\*\*用例名称\*\*\s*(.+?)\s*$", re.MULTILINE)

# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a blade-ai capability generator. Given a fault drill use-case .md file, derive three injection command variants DIRECTLY from the case content. The case library is authoritative — do not invent fault types, parameters or injection methods that the case does not describe.

## Generation rules

For the given use-case .md, output JSON:
```json
{{
  "inject_kind": "blade" | "kubectl" | "mixed",
  "title": "short case title (a few words, no category repetition)",
  "fault_symptom": "one-line fault symptom description",
  "scope": "", "target": "", "action": "",
  "params": [
    {{ "name": "<param-name>", "kind": "target_resource" | "fault_param", "resolved_from": "environment" | "user", "required": true, "default": "", "description": "what this slot is" }}
  ],
  "nl_cmd": "blade-ai inject -i \"<natural language description with <namespace>, <name>, <kubeconfig> placeholders>\"",
  "structured_cmd": "blade-ai inject --scope <s> --target <t> --action <a> [--labels app=<app>|-n <node>] [--namespace <ns>] --params <k=v,...> --kubeconfig <kubeconfig>",
  "direct_cmd": "blade-ai inject --direct --scope <s> --target <t> --action <a> ... --params ... --kubeconfig <kubeconfig>",
  "direct_hint": ""
}}
```

### Capability definition (scope / target / action / params)
- scope/target/action: the SAME values used in structured_cmd, as plain strings (no flags). ALWAYS fill them for every inject_kind. Derivation is UNIFORM for all cases: read what fault the case injects and express it as scope/target/action. The only thing inject_kind selects is which provider vocabulary the triple must stay within:
  - blade / mixed (the blade part): ChaosBlade family vocabulary — k8s family: scope ∈ pod|node|container, target is the OS subsystem (cpu|mem|disk|network|process), action is the blade verb (fullload|fill|delay|loss|drop|kill|burn|stop...); host family: scope=host with the same target/action set; python family: scope=python, target is the middleware client (http|redis|mysql|kafka|...), action ∈ delay|throwCustomException|returnValue.
  - kubectl: kubectl-native vocabulary — scope ∈ pod|node|container|deployment|statefulset|daemonset|service; target ∈ pod|finalizer|replicas|schedule|pvc|dns|image|probe|volume|cni|endpoint|resources|file; action ∈ patch|cordon|taint|delete|drain|scale|corrupt|duplicate.
  Never mix the two vocabularies. Pick the verb that CREATES the fault (recovery verbs like uncordon are never actions). Examples: pod CPU 打满→pod/cpu/fullload(blade)；节点排空维护→node/schedule/drain；节点污点注入→node/schedule/taint；标记节点不可调度→node/schedule/cordon；副本缩容→deployment/replicas/scale；Finalizer 阻塞删除→pod/finalizer/patch；probe 配置篡改→deployment/probe/patch；镜像拉取失败→deployment/image/patch；PVC 未绑定→deployment/pvc/patch；CoreDNS NXDOMAIN 拦截→deployment/dns/patch；网络包损坏→pod/cni/corrupt；网络包重复→pod/cni/duplicate；Service selector 不匹配→service/endpoint/patch.
- params: the complete parameter schema of this capability, covering BOTH the victim addressing slots and the fault tuning knobs:
  - victim addressing slots: kind="target_resource", resolved_from="environment" (the drill environment provides them). Examples: the node/host name for `-n <host>`, the pod selector for `--labels app=<app>` (use name "labels"), namespace for `--namespace <ns>` (use name "namespace"), kubeconfig path when the command takes `--kubeconfig <kubeconfig>` (use name "kubeconfig"). Mark required=true.
  - fault tuning knobs appearing in `--params k=v` (e.g. cpu-percent, mem-percent, percent, timeout, path, port, domain): kind="fault_param", resolved_from="user", default=the value used in structured_cmd (empty string if a bare boolean flag), description=what it controls.
  - required=false for optional knobs; never invent parameters the case does not use.

### Three command types
1. **nl_cmd** (always generate): Natural language intent `blade-ai inject -i "..."`, the LLM reads docs and executes
2. **structured_cmd** (always generate): Structured params (no --direct), LLM executes based on spec + docs (more precise than NL)
3. **direct_cmd** (blade primitives only): With --direct, deterministic injection skipping LLM; leave empty for control-plane cases

### inject_kind classification
- **blade**: The use-case's real injection method is a blade resource stress primitive (cpu/mem/disk/network/process — including host OS-level and python in-process blade experiments)
- **kubectl**: The use-case's real injection method is a kubectl control-plane operation (PVC/Taint/probe/image/finalizer/scale/cordon etc, no blade primitive). Only k8s-scope cases may be kubectl.
- **mixed**: Requires blade injection + kubectl cooperation to manifest symptoms (e.g., kubectl delete pod first, then blade network drop)

### structured_cmd rules
- Format: `blade-ai inject --scope <s> --target <t> --action <a> <SEL> [--namespace <ns>] --params <P> --kubeconfig <kubeconfig>`
- <SEL>: node scope uses `-n <node>`; pod/container uses `--labels app=<app>`; host scope uses `-n <host>`; python scope uses `-n <app-host>`
- namespace-less scopes (node, host, python) omit --namespace; pod/container includes --namespace <ns>; host and python scopes also omit --kubeconfig
- --params: comma-separated k=v pairs; boolean flags (read/write etc) as bare keys. Example: `path=/data,read,write`. Do NOT quote the value.
- scope/target/action are derived from the case's injection description. Resource addressing per ChaosBlade family:
  - k8s family: `<scope>-<target>` resource (e.g. pod-cpu) — scope is pod/node/container
  - host family: bare target (e.g. cpu) — use scope=host with that target
  - python family: use scope=python, target is the middleware client (http/redis/mysql/kafka/...)
- **inject_kind=blade or mixed**: generate structured_cmd with the blade scope/target/action described by the case
- **inject_kind=kubectl**: generate structured_cmd with the kubectl-native scope/target/action derived above (the structured intent path routes it to the kubectl-native backend — no blade binary command exists, but the triple IS the capability definition)

### direct_cmd rules (blade primitives only)
- Same format as structured_cmd but with `--direct` prepended
- **Only generate when inject_kind=blade or the blade part of mixed**
- Flags in --params follow the case's injection parameters and standard ChaosBlade flag names — do NOT use abbreviations (e.g., use `network-traffic=out` not bare `out`)
- Parameter values: use specific values from the .md when available (e.g., path=/var/lib/containerd); otherwise use placeholders <...> or safe defaults (cpu-percent=80, mem-percent=90, percent=90, path=/data etc)
- For control-plane cases (inject_kind=kubectl): direct_cmd="" and direct_hint="Control-plane fault (kubectl scale/patch), no blade direct command available. Use nl_cmd or structured_cmd instead."

### mixed handling
- structured_cmd: full blade structured params
- direct_cmd: blade part only (if the blade part can independently inject)
- direct_hint: describe prerequisite/postrequisite kubectl commands (e.g., "Requires: kubectl delete pod <pod> -n <ns> before injection")
"""

# Language-specific tail appended to _SYSTEM_PROMPT. Default is English;
# ``lang="cn"`` selects Simplified Chinese descriptions.
_LANGUAGE_SECTIONS = {
    "en": """
### Language
- Write `title`, `fault_symptom`, every params[].description and the natural-language description inside `nl_cmd` in English.
- Keep ChaosBlade/K8s terminology, flags, parameter names and placeholder tokens (<...>) in their original form.

Output JSON only, no other content.""",
    "cn": """
### Language
- Write `title`, `fault_symptom`, every params[].description and the natural-language description inside `nl_cmd` in Simplified Chinese (简体中文).
- Keep ChaosBlade/K8s terminology, flags, parameter names and placeholder tokens (<...>) in their original form.

Output JSON only, no other content.""",
}

# CLI lang value → registry `lang` tag / bundled artifact name.
_LANG_TAG = {"en": "en", "cn": "zh"}

# Skill name → capability family. Unknown skills fall back to a scope-based
# derivation from fault_registry (python_* → python, host_* → host, else k8s).
_FAMILY_BY_SKILL = {
    "k8s-chaos-skills": "k8s",
    "host-chaos-skills": "host",
    "python-app-chaos-skills": "python",
}


def _family_of(skill: str, scope: str) -> str:
    if skill in _FAMILY_BY_SKILL:
        return _FAMILY_BY_SKILL[skill]
    try:
        from chaos_agent.agent.spec import fault_registry as fr
        if fr.is_python_scope(scope):
            return "python"
        if fr.is_host_scope(scope):
            return "host"
    except Exception:  # noqa: BLE001
        pass
    return "k8s"


def _profile_of(scope: str) -> str:
    """Environment channel profile required to inject this scope."""
    try:
        from chaos_agent.agent.spec import fault_registry as fr
        return fr.profile_of_scope(scope)
    except Exception:  # noqa: BLE001
        return "k8s"


def _cluster_scoped_set() -> frozenset[str]:
    try:
        from chaos_agent.agent.spec import fault_registry as fr
        return fr.aggregate_cluster_scoped()
    except Exception:  # noqa: BLE001
        return frozenset({"host", "node", "python"})


def _slug(text: str) -> str:
    """ASCII-safe slug for capability ids (CJK chars are dropped)."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s or "case"


def _derive_title(use_case_name: str, llm_title: str) -> str:
    if llm_title:
        return llm_title
    # "进程CPU满载 导致 Host_CPU使用率过高" → "进程CPU满载"
    return use_case_name.split(" 导致 ")[0].strip() or use_case_name


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _parse_llm_json(content: str) -> dict:
    """Parse JSON from LLM response text (handles markdown code blocks)."""
    text = content.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3].rstrip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}


async def _llm_invoke(llm, messages: list) -> tuple[dict, str]:
    """Invoke LLM with messages, return (parsed_dict, raw_content)."""
    response = await llm.ainvoke(messages)
    content = response.content if isinstance(response.content, str) else str(response.content)
    if not content.strip():
        rc = getattr(response, "additional_kwargs", {}).get("reasoning_content", "")
        if rc:
            content = rc
    return _parse_llm_json(content), content


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

async def sync_capabilities(
    catalogue_roots: dict[str, Path],
    llm,
    output_path: Path,
    lang: str = "en",
    reference_registry: Path | None = None,
) -> dict:
    """Sync: LLM derives commands from each case's markdown → write JSON.

    Args:
        catalogue_roots: Mapping of skill name → its references/catalogue/
            directory. Every fault-injection skill with a catalogue is
            synced in one run; callers filter by skill_type beforehand.
        llm: LangChain LLM instance (from make_llm)
        output_path: Where to write the result JSON
        lang: Language of nl_cmd/fault_symptom prose — "en" (default) or "cn"
        reference_registry: Optional v2 registry file (usually the other
            language's committed artifact). When given, each case's
            structural fields (triple/params/commands) are anchored to the
            matching case_path entry so both languages stay id-isomorphic;
            only prose fields are re-generated in this run's language.

    Returns:
        The full catalog dict (also written to output_path)
    """
    lang_key = (lang or "en").strip().lower()
    if lang_key not in _LANGUAGE_SECTIONS:
        raise ValueError(f"Unsupported lang {lang!r}; expected one of {sorted(_LANGUAGE_SECTIONS)}")
    system_prompt = _SYSTEM_PROMPT + _LANGUAGE_SECTIONS[lang_key]

    # Collect all tasks (metadata + md content) across every skill catalogue
    tasks: list[dict] = []
    for skill_name, catalogue_root in catalogue_roots.items():
        for cat_dir in sorted(p for p in catalogue_root.iterdir() if p.is_dir()):
            category = cat_dir.name
            for md_file in sorted(cat_dir.glob("*.md")):
                md_content = md_file.read_text(encoding="utf-8")
                # Content-first naming: the md's own **用例名称** line is
                # authoritative, so files may be named freely. Filename
                # derivation ({category}_{root_cause}.md) is the fallback.
                name_m = _CASE_NAME_RE.search(md_content)
                if name_m:
                    use_case_name = name_m.group(1)
                else:
                    stem = md_file.stem
                    prefix = category + "_"
                    root_cause = stem[len(prefix):] if stem.startswith(prefix) else stem
                    use_case_name = f"{root_cause} 导致 {category}"
                tasks.append({
                    "skill": skill_name,
                    "category": category,
                    "use_case_name": use_case_name,
                    "resource_path": str(md_file.relative_to(catalogue_root.parent.parent)),
                    "stem": md_file.stem,
                    "md_content": md_content,
                })

    CONCURRENCY = 10

    completed_count = 0
    total_count = len(tasks)

    _KIND_ICON = {"blade": "B", "kubectl": "K", "mixed": "M", "unknown": "?"}

    def _print_progress(done: int, total: int, name: str, kind: str, status: str) -> None:
        bar_width = 20
        filled = int(bar_width * done / total) if total else bar_width
        bar = "#" * filled + "-" * (bar_width - filled)
        icon = _KIND_ICON.get(kind, "?")
        mark = f"[{icon}]" if status == "ok" else "[X]"
        pct = int(100 * done / total) if total else 100
        sys.stderr.write(f"\r  [{bar}] {pct:3d}% ({done}/{total}) {mark} {name[:45]}\033[K\n")
        sys.stderr.flush()

    MAX_RETRIES = 3

    async def _process_one(task_info: dict) -> dict:
        nonlocal completed_count
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        stem = task_info["stem"]
        logger.info("  Processing: %s", task_info["use_case_name"])

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=task_info["md_content"]),
        ]

        result = {}
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                result, raw = await _llm_invoke(llm, messages)
            except Exception as e:
                # Transport-level failure: the LLM client already applies its
                # own retry budget (settings.llm_max_retries + the resilient
                # wrapper), so a failure surfacing here is terminal for this
                # case — re-invoking immediately would hit the same outage.
                logger.error("    LLM failed for %s (attempt %d): %s", stem, attempt, e)
                result = {}
                break

            if not result:
                # The model answered but the content wasn't valid JSON.
                # That's fixable by the model itself — feed the parse
                # failure back and retry within the same budget instead of
                # giving up silently.
                if attempt < MAX_RETRIES:
                    logger.info("    Retry %d/%d for %s: response was not valid JSON", attempt, MAX_RETRIES, stem)
                    messages.append(AIMessage(content=raw))
                    messages.append(HumanMessage(content=(
                        "Your response could not be parsed as JSON. Output ONLY the JSON object "
                        "described in the system prompt — no markdown fences, no commentary."
                    )))
                    continue

            break

        if not result:
            completed_count += 1
            _print_progress(completed_count, total_count, task_info["use_case_name"], "unknown", "FAILED")
            return {
                "skill": task_info["skill"],
                "category": task_info["category"],
                "use_case_name": task_info["use_case_name"],
                "resource_path": task_info["resource_path"],
                "fault_symptom": "",
                "inject_kind": "unknown",
                "nl_cmd": "",
                "structured_cmd": "",
                "direct_cmd": "",
                "direct_hint": "LLM generation failed. Re-run blade-ai capabilities-sync.",
            }

        # 控制面（kubectl-native）case 没有 blade --direct 确定性命令：
        # direct_cmd 强制为空。三元组与 structured_cmd 保留——kubectl-native
        # provider 按三元组注入/反向恢复（providers/k8s_native.py），
        # 「无 blade 命令」不等于「不可执行」
        inject_kind = result.get("inject_kind", "unknown")
        if str(inject_kind).lower() == "kubectl":
            result["direct_cmd"] = ""

        completed_count += 1
        _print_progress(completed_count, total_count, task_info["use_case_name"], inject_kind, "ok")

        return {
            "skill": task_info["skill"],
            "category": task_info["category"],
            "use_case_name": task_info["use_case_name"],
            "resource_path": task_info["resource_path"],
            "stem": stem,
            "fault_symptom": result.get("fault_symptom", ""),
            "inject_kind": inject_kind,
            "title": str(result.get("title", "") or "").strip(),
            "scope": str(result.get("scope", "") or "").strip(),
            "target": str(result.get("target", "") or "").strip(),
            "action": str(result.get("action", "") or "").strip(),
            "params": result.get("params", []) if isinstance(result.get("params"), list) else [],
            "nl_cmd": result.get("nl_cmd", ""),
            "structured_cmd": result.get("structured_cmd", ""),
            "direct_cmd": result.get("direct_cmd", ""),
            "direct_hint": result.get("direct_hint", ""),
        }

    sem = asyncio.Semaphore(CONCURRENCY)

    async def _bounded(t):
        async with sem:
            return await _process_one(t)

    raw_results = await asyncio.gather(*[_bounded(t) for t in tasks], return_exceptions=True)
    cases = [r for r in raw_results if isinstance(r, dict)]

    if reference_registry is not None:
        cases = _align_with_reference(cases, reference_registry)

    # Stats (computed after gather, no concurrency issue)
    stats: dict[str, int] = {}
    for c in cases:
        kind = c.get("inject_kind", "unknown")
        stats[kind] = stats.get(kind, 0) + 1

    catalog = build_registry(cases, lang=lang)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(output_path.parent), suffix=".json.tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(catalog, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(output_path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    logger.info(
        "Sync complete: %d cases (blade=%d, kubectl=%d, mixed=%d)",
        len(cases), stats.get("blade", 0), stats.get("kubectl", 0),
        stats.get("mixed", 0),
    )
    return catalog


# ---------------------------------------------------------------------------
# Cross-language alignment
# ---------------------------------------------------------------------------

def align_cases_with_reference(cases: list[dict], reference_registry: Path) -> list[dict]:
    """Anchor structural fields of ``cases`` to a reference v2 registry.

    Public wrapper (used by maintainer tooling and tests); see
    ``sync_capabilities(reference_registry=...)``.
    """
    return _align_with_reference(cases, reference_registry)


def _align_with_reference(cases: list[dict], reference_registry: Path) -> list[dict]:
    try:
        ref = json.loads(Path(reference_registry).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Cannot read reference registry %s: %s — skipping alignment", reference_registry, e)
        return cases
    ref_by_path = {
        c.get("case_path"): c
        for c in ref.get("cases", [])
        if isinstance(c, dict) and c.get("case_path")
    }
    aligned = 0
    for c in cases:
        ref_case = ref_by_path.get(c.get("resource_path"))
        if not ref_case:
            continue
        for field in _STRUCTURAL_FIELDS:
            if field == "params" or field not in ref_case:
                continue
            c[field] = ref_case[field]
        # params：槽位结构（名称/类型/解析来源/默认值）锚定参考注册表，
        # 同名槽位的 description 保留本次语言生成的文本
        ref_params = ref_case.get("params")
        if isinstance(ref_params, list):
            local_desc = {
                p.get("name"): str(p.get("description", "") or "")
                for p in c.get("params", []) if isinstance(p, dict)
            }
            merged: list[dict] = []
            for rp in ref_params:
                if not isinstance(rp, dict):
                    continue
                np = dict(rp)
                if local_desc.get(np.get("name")):
                    np["description"] = local_desc[np["name"]]
                merged.append(np)
            c["params"] = merged
        # Prose fields stay as freshly generated; fall back to the reference
        # text when this run's LLM failed, so the case is never empty.
        for field in _PROSE_FIELDS:
            if not c.get(field) and ref_case.get(field):
                c[field] = ref_case[field]
        aligned += 1
    if aligned:
        logger.info("Anchored %d/%d cases to reference registry %s", aligned, len(cases), reference_registry)
    return cases


# ---------------------------------------------------------------------------
# v2 registry assembly (shared by LLM sync and v1 conversion)
# ---------------------------------------------------------------------------

def _normalize_param(raw: dict) -> dict | None:
    name = str(raw.get("name", "") or "").strip()
    if not name:
        return None
    kind = raw.get("kind", "fault_param")
    if kind not in ("target_resource", "fault_param"):
        kind = "fault_param"
    resolved_from = raw.get("resolved_from", "user")
    if resolved_from not in ("environment", "user"):
        resolved_from = "user"
    return {
        "name": name,
        "kind": kind,
        "resolved_from": "environment" if kind == "target_resource" else resolved_from,
        "required": bool(raw.get("required", kind == "target_resource")),
        "default": str(raw.get("default", "") or ""),
        "description": str(raw.get("description", "") or ""),
    }


def build_registry(cases: list[dict], lang: str = "en") -> dict:
    """Assemble the v2 registry envelope: derive family/profile/cluster_scoped/
    executable, assign deterministic ids and stamp the skills fingerprint.

    ``cases`` items carry the sync output fields (skill/category/use_case_name/
    resource_path/stem/scope/target/action/params/commands...).
    """
    cluster_scoped = _cluster_scoped_set()
    id_counts: dict[str, int] = {}
    out_cases: list[dict] = []
    for c in cases:
        scope = str(c.get("scope", "") or "")
        target = str(c.get("target", "") or "")
        action = str(c.get("action", "") or "")
        inject_kind = str(c.get("inject_kind", "unknown") or "unknown")
        # executable = 三元组完整：blade 与 kubectl-native 两类后端都按三元组
        # 注入；direct_cmd 为空（无 blade --direct）不影响可执行性
        executable = bool(scope and target and action)
        family = _family_of(str(c.get("skill", "")), scope)
        profile = _profile_of(scope) if executable else ("host" if family in ("host", "python") else "k8s")

        if executable:
            base_id = f"{family}.{scope}.{target}.{action}"
        else:
            # Stable slug from the md stem minus the category prefix — CJK
            # drops out of _slug, so keep a short hash to stay unique/stable.
            stem = str(c.get("stem", "") or Path(str(c.get("resource_path", ""))).stem)
            prefix = str(c.get("category", "") or "") + "_"
            variant = stem[len(prefix):] if stem.startswith(prefix) else stem
            variant_hash = hashlib.md5(stem.encode()).hexdigest()[:6] if not variant.isascii() else _slug(variant)
            base_id = f"{family}.{_slug(str(c.get('category', '')))}.{variant_hash}"
        n = id_counts.get(base_id, 0)
        id_counts[base_id] = n + 1
        case_id = base_id if n == 0 else f"{base_id}.{n + 1}"

        params = [p for p in (_normalize_param(p) for p in c.get("params", []) if isinstance(p, dict)) if p]

        out_cases.append({
            "id": case_id,
            "skill": c.get("skill", ""),
            "family": family,
            "profile": profile,
            "scope": scope,
            "target": target,
            "action": action,
            "cluster_scoped": scope in cluster_scoped,
            "title": _derive_title(str(c.get("use_case_name", "")), str(c.get("title", "") or "")),
            "category": c.get("category", ""),
            "symptom": c.get("fault_symptom", ""),
            "case_path": c.get("resource_path", ""),
            "nl_cmd": c.get("nl_cmd", ""),
            "params": params,
            "executable": executable,
            # Back-compat fields for `blade-ai list` and existing consumers.
            "use_case_name": c.get("use_case_name", ""),
            "inject_kind": inject_kind,
            "structured_cmd": c.get("structured_cmd", ""),
            "direct_cmd": c.get("direct_cmd", ""),
            "direct_hint": c.get("direct_hint", ""),
        })

    try:
        fingerprint = _compute_sync_fingerprint()
    except Exception:  # noqa: BLE001 — fingerprint is best-effort metadata
        logger.warning("Failed to compute skills fingerprint", exc_info=True)
        fingerprint = ""

    return {
        "schema_version": SCHEMA_VERSION,
        "lang": _LANG_TAG.get((lang or "en").strip().lower(), "en"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skills_fingerprint": fingerprint,
        "total": len(out_cases),
        "cases": out_cases,
    }


def _compute_sync_fingerprint() -> str:
    from chaos_agent.skills.capabilities import compute_skills_fingerprint
    return compute_skills_fingerprint()


# ---------------------------------------------------------------------------
# v1 → v2 conversion (bootstrap bundled artifacts without an LLM run)
# ---------------------------------------------------------------------------


def _v1_params_from_cmd(structured_cmd: str, scope: str) -> list[dict]:
    """Deterministically derive a v2 params schema from a v1 structured_cmd."""
    params: list[dict] = []
    if scope in ("node", "host", "python") and re.search(r"(?:^|\s)-n\s+\S+", structured_cmd):
        params.append({"name": scope if scope != "python" else "app-host", "kind": "target_resource",
                       "resolved_from": "environment", "required": True, "default": "",
                       "description": "target name"})
    if "--labels" in structured_cmd:
        params.append({"name": "labels", "kind": "target_resource", "resolved_from": "environment",
                       "required": True, "default": "", "description": "pod label selector"})
    if "--namespace" in structured_cmd:
        params.append({"name": "namespace", "kind": "target_resource", "resolved_from": "environment",
                       "required": True, "default": "", "description": "kubernetes namespace"})
    if "--kubeconfig" in structured_cmd:
        params.append({"name": "kubeconfig", "kind": "target_resource", "resolved_from": "environment",
                       "required": True, "default": "", "description": "kubeconfig path"})
    m = re.search(r"--params\s+(\S+)", structured_cmd)
    if m:
        for pair in m.group(1).split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params.append({"name": k.strip(), "kind": "fault_param", "resolved_from": "user",
                               "required": False, "default": v.strip(), "description": k.strip()})
            elif pair.strip():
                params.append({"name": pair.strip(), "kind": "fault_param", "resolved_from": "user",
                               "required": False, "default": "", "description": pair.strip()})
    return params


def convert_v1_to_v2(v1: dict, lang: str = "en") -> dict:
    """Convert a v1 catalog (old skill_capabilities.json shape) into a v2
    registry WITHOUT any LLM call — deterministic parsing only.

    Used by maintainers to bootstrap the package-bundled artifacts when a v1
    file already exists; prose fields (symptom/nl_cmd) keep the v1 language,
    a proper localized regeneration is `blade-ai capabilities-sync --lang …`.
    """
    triple_re = re.compile(r"--scope\s+(\S+)\s+--target\s+(\S+)\s+--action\s+(\S+)")
    cases: list[dict] = []
    for c in v1.get("cases", []):
        structured_cmd = c.get("structured_cmd", "") or ""
        m = triple_re.search(structured_cmd)
        scope, target, action = (m.group(1), m.group(2), m.group(3)) if m else ("", "", "")
        stem = Path(c.get("resource_path", "")).stem
        cases.append({
            "skill": c.get("skill", ""),
            "category": c.get("category", ""),
            "use_case_name": c.get("use_case_name", ""),
            "resource_path": c.get("resource_path", ""),
            "stem": stem,
            "fault_symptom": c.get("fault_symptom", ""),
            "inject_kind": c.get("inject_kind", "unknown"),
            "title": "",
            "scope": scope,
            "target": target,
            "action": action,
            "params": _v1_params_from_cmd(structured_cmd, scope),
            "nl_cmd": c.get("nl_cmd", ""),
            "structured_cmd": structured_cmd,
            "direct_cmd": c.get("direct_cmd", ""),
            "direct_hint": c.get("direct_hint", ""),
        })
    return build_registry(cases, lang=lang)
