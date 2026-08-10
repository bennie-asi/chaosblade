"""Environment-bound case values must stay placeholders, not copy-paste literals.

Regression guard for the sess_6645c3eaa130 incident: the intent node copied
the skill-case example literal ``port: 8080`` verbatim into the submitted
intent's ``params``; downstream preserved it as the user-approved contract,
and the planner even labelled it "the user's specified port". The fault
mechanism silently shifted from "probe timing too aggressive" to "probe
points at an unlistened port".

Defence has two layers; this test guards the case-content layer. Values that
bind to the target environment (probe port/path, runtime data directory) are
written as ``<占位符>`` so there is nothing concrete an intent node could
mistake for user intent. The complementary intent-node layer (the Provenance
rule in ``prompts/sections/intent.py``) is guarded by ``test_prompts.py``.
"""

from pathlib import Path

import pytest

_SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"
_K8S_CATALOGUE = _SKILLS_DIR / "k8s-chaos-skills" / "references" / "catalogue"

# (case file, banned literals, required placeholders)
_CASES = [
    (
        _K8S_CATALOGUE / "Pod_CrashLoopBackOff" / "Pod_CrashLoopBackOff_LivenessProbe配置不合理.md",
        ["port: 8080", "path: /healthz"],
        ["<应用实际健康检查端口>", "<应用实际健康检查路径>"],
    ),
    (
        _K8S_CATALOGUE / "Pod_CrashLoopBackOff" / "Pod_CrashLoopBackOff_StartupProbe配置不足.md",
        ["port: 8080", "path: /healthz"],
        ["<应用实际健康检查端口>", "<应用实际健康检查路径>"],
    ),
    (
        _K8S_CATALOGUE / "Service_调用失败" / "Service_调用失败_ReadinessProbe配置不一致.md",
        ["port: 9999", "path: /non-existent-health-path"],
        ["<应用不监听的端口>", "<应用不提供的路径>"],
    ),
    (
        _K8S_CATALOGUE / "Pod_被驱逐重建" / "Pod_被驱逐重建_DiskPressure.md",
        ["--path /var/lib/containerd"],
        ["<容器运行时数据目录>"],
    ),
]


@pytest.mark.parametrize(
    ("case_path", "banned", "required"),
    _CASES,
    ids=[p.parent.name + "/" + p.name for p, _, _ in _CASES],
)
def test_environment_bound_values_are_placeholders(case_path, banned, required):
    text = case_path.read_text(encoding="utf-8")
    for literal in banned:
        assert literal not in text, (
            f"{case_path.name}: copy-paste literal {literal!r} is back — "
            "an intent node could submit it as user intent"
        )
    for placeholder in required:
        assert placeholder in text, (
            f"{case_path.name}: placeholder {placeholder!r} missing — the "
            "environment-bound value must stay non-concrete"
        )
