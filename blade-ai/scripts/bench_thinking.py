"""Benchmark: thinking on/off for the exact aux LLM calls of task-7b3c554a (k2).

Replays the byte-identical prompts recorded in k2 aux_llm_calls through the
same make_llm() factory + local config (~/.blade-ai/config.json, qwen3.8-max),
toggling only enable_thinking. Measures wall time + captures usage/reasoning.

Run:  .venv/bin/python scripts/bench_thinking.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from chaos_agent.agent.factory import make_llm  # noqa: E402
from chaos_agent.agent.postmortem.generator import (  # noqa: E402
    _SYSTEM_PROMPT as PM_SYSTEM_PROMPT,
    _USER_PROMPT_TEMPLATE as PM_USER_TEMPLATE,
)
from chaos_agent.config.settings import settings  # noqa: E402

K2 = REPO / "k2"
OUT = REPO / "bench_thinking_results.json"


def load_aux_requests() -> dict[str, str]:
    data = json.loads(K2.read_text())
    out: dict[str, str] = {}
    for call in data.get("aux_llm_calls", []):
        out.setdefault(call["purpose"], call["request"])
    return out


def split_baseline(request: str) -> tuple[str, str]:
    # recorded as f"{_sys}\n\n---\n\n{human_prompt}" (_llm_derive.py L115)
    sep = "\n\n---\n\n"
    idx = request.find(sep)
    assert idx > 0, "cannot locate system/human boundary in baseline request"
    return request[:idx], request[idx + len(sep):]


def build_postmortem_messages(context_json: str) -> list:
    # terminal_reports records only the context JSON; the System/User prompts
    # are code constants — rebuild byte-identical to generate_postmortem().
    return [
        SystemMessage(content=PM_SYSTEM_PROMPT),
        HumanMessage(content=PM_USER_TEMPLATE.format(context_json=context_json)),
    ]


async def run_one(name: str, messages: list, thinking: bool) -> dict:
    llm = make_llm(enable_thinking=thinking)
    tag = f"{name} | thinking={'ON' if thinking else 'OFF'}"
    print(f"[start] {tag}", flush=True)
    t0 = time.perf_counter()
    try:
        resp = await asyncio.wait_for(llm.ainvoke(messages), timeout=600)
        err = None
    except Exception as e:  # noqa: BLE001
        resp, err = None, f"{type(e).__name__}: {e}"
    dt = time.perf_counter() - t0

    content = getattr(resp, "content", "") or "" if resp else ""
    reasoning = ""
    if resp is not None:
        reasoning = (getattr(resp, "additional_kwargs", {}) or {}).get(
            "reasoning_content", ""
        ) or ""
    usage = getattr(resp, "usage_metadata", None) if resp else None
    rec = {
        "case": name,
        "thinking": thinking,
        "wall_seconds": round(dt, 2),
        "error": err,
        "content_chars": len(content),
        "reasoning_chars": len(reasoning),
        "usage": dict(usage) if usage else None,
        "content_head": content[:300],
    }
    print(
        f"[done ] {tag}: {dt:.1f}s, content={len(content)}c, "
        f"reasoning={len(reasoning)}c, usage={rec['usage']}, err={err}",
        flush=True,
    )
    return rec


async def main() -> None:
    print(
        f"model={settings.model_name} base_url={settings.api_base_url} "
        f"default_enable_thinking={settings.llm_enable_thinking}",
        flush=True,
    )
    aux = load_aux_requests()
    b_sys, b_hum = split_baseline(aux["baseline_derive"])
    # terminal_reports records the context JSON without indent; the real
    # call re-serializes with indent=2 (generator.py) — reproduce that.
    pm_context_json = json.dumps(
        json.loads(aux["postmortem"]), ensure_ascii=False, indent=2, default=str,
    )
    pm_msgs = build_postmortem_messages(pm_context_json)
    print(
        f"baseline prompt: sys={len(b_sys)}c hum={len(b_hum)}c | "
        f"postmortem prompt: sys={len(PM_SYSTEM_PROMPT)}c hum={len(pm_msgs[1].content)}c",
        flush=True,
    )
    baseline_msgs = [SystemMessage(content=b_sys), HumanMessage(content=b_hum)]

    results = []
    # OFF first (fast), then ON; sequential to avoid provider-side coupling.
    results.append(await run_one("baseline_derive", baseline_msgs, False))
    results.append(await run_one("baseline_derive", baseline_msgs, True))
    results.append(await run_one("postmortem", pm_msgs, False))
    results.append(await run_one("postmortem", pm_msgs, True))

    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print("\n===== SUMMARY =====")
    print(f"{'case':<16} {'thinking':<8} {'wall(s)':<9} {'content':<9} {'reason':<9}")
    for r in results:
        print(
            f"{r['case']:<16} {'ON' if r['thinking'] else 'OFF':<8} "
            f"{r['wall_seconds']:<9} {r['content_chars']:<9} {r['reasoning_chars']:<9}"
        )
    print(f"\nfull records -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
