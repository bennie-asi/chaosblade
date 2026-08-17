"""Per-model context budgets (knowledge table, pure data).

Migrated verbatim from ``config/settings.py`` so the whole model-
connection concern (endpoint dialects + model knowledge) lives under
``chaos_agent/llm/``. ``Settings.resolve_context_budget()`` remains
the single resolution entry point (user ``model_budgets`` > this
built-in table > global fallback).

Each entry maps a model-name PREFIX (case-insensitive) to its context
window size + the compact_ratio appropriate for that window. The
resolver picks the longest matching prefix.

Maintenance rules (locked by tests/test_config/test_settings.py):
- Longest-prefix-wins makes ordering irrelevant for correctness, but
  keep specific entries above generic fallbacks for readability
  (``qwen3.7-plus`` above ``qwen-plus``).
- Conservative floor: when a prefix covers several variants, use the
  smallest window among them to avoid ``context_length_exceeded``.
- Window sources: provider docs (claude.ai/docs, platform.openai.com,
  dashscope.aliyun.com, deepseek docs, bigmodel.cn).
- Compact-ratio rationale: smaller/cheaper models can fill more of the
  window before compacting (0.90); models with large windows leave
  more headroom for tool outputs (0.80–0.85).
"""

from __future__ import annotations

DEFAULT_MODEL_BUDGETS: dict[str, dict[str, float | int]] = {
    # Anthropic — Opus/Sonnet 4.6 起 1M 窗口已 GA（2026-03 标准定价），
    # 5 代在 API 上恒为 1M；4.5 及更早代际仍是 200K，由泛化前缀兜底
    "claude-opus-5":    {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "claude-sonnet-5":  {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "claude-opus-4-8":  {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "claude-opus-4-7":  {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "claude-opus-4-6":  {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "claude-sonnet-4-8": {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "claude-sonnet-4-7": {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "claude-sonnet-4-6": {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "claude-opus":      {"max_tokens": 200_000, "compact_ratio": 0.85},
    "claude-sonnet":    {"max_tokens": 200_000, "compact_ratio": 0.85},
    "claude-haiku":     {"max_tokens": 200_000, "compact_ratio": 0.90},
    # OpenAI
    "gpt-5":            {"max_tokens": 400_000, "compact_ratio": 0.80},
    "gpt-4.1":          {"max_tokens": 1_047_576, "compact_ratio": 0.80},
    "gpt-4o":           {"max_tokens": 128_000, "compact_ratio": 0.85},
    "gpt-4":            {"max_tokens": 128_000, "compact_ratio": 0.85},
    "o1":               {"max_tokens": 128_000, "compact_ratio": 0.85},
    "o3":               {"max_tokens": 200_000, "compact_ratio": 0.85},
    "o4-mini":          {"max_tokens": 200_000, "compact_ratio": 0.90},
    # Google Gemini — 2.x 全系（2.0/2.5）与 3.x 同为 1M 窗口
    "gemini-3":         {"max_tokens": 1_048_576, "compact_ratio": 0.80},
    "gemini-2.5":       {"max_tokens": 1_048_576, "compact_ratio": 0.80},
    "gemini-2":         {"max_tokens": 1_048_576, "compact_ratio": 0.80},
    # Alibaba Qwen (DashScope) — 窗口值取自百炼官方模型列表：
    # 3.8 / 3.7 全系 1M；3.6-max / 3-max 为 256k；3.5 代整代下界 256k；
    # qwen3-coder-plus/flash 1M，coder-next 256k（泛化 coder 取下界）
    "qwen3.8-max":      {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "qwen3.8":          {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "qwen3.7-max":      {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "qwen3.7-plus":     {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "qwen3.7":          {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "qwen3.6-max":      {"max_tokens": 262_144, "compact_ratio": 0.80},
    "qwen3.6-plus":     {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "qwen3.5":          {"max_tokens": 262_144, "compact_ratio": 0.80},
    "qwen3-max":        {"max_tokens": 262_144, "compact_ratio": 0.80},
    "qwen3-coder-plus": {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "qwen3-coder-flash": {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "qwen3-coder":      {"max_tokens": 262_144, "compact_ratio": 0.80},
    "qwen3":            {"max_tokens": 131_072, "compact_ratio": 0.80},
    # qwen-max/plus 前缀覆盖新旧全部快照，取历史下界 32K 保守兜底；
    # -latest 滚动别名已升至 1M（百炼官方），最长前缀单独登记不影响
    # 带日期快照的保守兜底
    "qwen-plus-latest": {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "qwen-max":         {"max_tokens":  32_768, "compact_ratio": 0.80},
    "qwen-plus":        {"max_tokens":  32_768, "compact_ratio": 0.80},
    "qwen-turbo":       {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "qwen-flash":       {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    # DeepSeek — V4（2026-04）起官方 chat/reasoner 端点升至 1M 窗口；
    # 泛化 "deepseek" 保持 64K 作为老版本/第三方部署的保守兜底
    "deepseek-v4":      {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "deepseek-chat":    {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "deepseek-reasoner": {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "deepseek":         {"max_tokens":  64_000, "compact_ratio": 0.80},
    # Zhipu GLM — 5.2 升至 1M；5.0/5.1 与 4.6 为 200K；6.x 起新世代
    # 官方默认 1M（对齐 qwen-code 的前瞻登记）
    "glm-6":            {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "glm-5.2":          {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "glm-5":            {"max_tokens": 200_000, "compact_ratio": 0.85},
    "glm-4.6":          {"max_tokens": 200_000, "compact_ratio": 0.85},
    "glm-4":            {"max_tokens": 128_000, "compact_ratio": 0.85},
    # Moonshot Kimi — K3（2026-07）1M（max_completion_tokens 上限
    # 1048576 = 2^20，故窗口取二进制 1M）；K2 系列 256K
    "kimi-k3":          {"max_tokens": 1_048_576, "compact_ratio": 0.80},
    "kimi-k2":          {"max_tokens": 262_144, "compact_ratio": 0.80},
    "moonshot":         {"max_tokens": 131_072, "compact_ratio": 0.85},
    # xAI Grok
    "grok-4-fast":      {"max_tokens": 2_000_000, "compact_ratio": 0.80},
    "grok-4":           {"max_tokens": 256_000, "compact_ratio": 0.80},
    # ByteDance — doubao-seed 为商用端点系列；seed-oss 是字节开源
    # Seed-OSS 系列模型（512K 窗口，对齐 qwen-code）
    "doubao-seed":      {"max_tokens": 256_000, "compact_ratio": 0.80},
    "seed-oss":         {"max_tokens": 524_288, "compact_ratio": 0.80},
    # MiniMax — M3 1M、M2.5 192K（196,608 = 3×64K），泛化兜底 200K
    "minimax-m3":       {"max_tokens": 1_000_000, "compact_ratio": 0.80},
    "minimax-m2.5":     {"max_tokens": 196_608, "compact_ratio": 0.80},
    "minimax":          {"max_tokens": 200_000, "compact_ratio": 0.80},
}

#: Minimum resolved context window for an aux call to skip thinking.
#: The 6-75x latency wins behind aux-call thinking-disable were all
#: measured on STRONG models (bench_thinking.py); a weaker model pays
#: a quality price instead. Window size is the capability proxy we
#: can check offline: >= 1M marks the flagship tier of every vendor
#: table above, while anything below (incl. the 100K global fallback
#: that unknown/unregistered models land on) keeps thinking ON for
#: aux calls. Quality-guarantee knob — deliberately a constant, not a
#: user-facing setting.
AUX_THINKING_SKIP_MIN_WINDOW: int = 1_000_000


def window_allows_thinking_skip(max_tokens: int) -> bool:
    """True iff a model with this context window is strong enough for
    aux calls (baseline derivation, postmortem, display catalogs) to
    ship an explicit thinking-disable without quality risk.
    """
    return max_tokens >= AUX_THINKING_SKIP_MIN_WINDOW
