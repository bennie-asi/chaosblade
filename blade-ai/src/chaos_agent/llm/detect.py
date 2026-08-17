"""Endpoint detection: map a base URL to a :class:`CompatSpec`.

Resolution priority (openclaw's three-tier rule):

1. explicit user override (``Settings.llm_thinking_format`` != "auto")
2. URL auto-detection (this module)
3. conservative default — :attr:`ThinkingFormat.NONE`

Hostnames are parsed with :mod:`urllib.parse` and matched exactly or
as a parent domain, never with regex against attacker-influenced URLs
(qwen-code's guard against ReDoS and path-spoofing like
``https://evil.example/dashscope.aliyuncs.com/...``).
"""

from __future__ import annotations

from urllib.parse import urlparse

from chaos_agent.llm.compat import CompatSpec, ThinkingFormat

#: Official DashScope regional hosts. A hostname matches when it equals
#: one of these or is a subdomain of one.
DASHSCOPE_REGIONAL_HOSTS: tuple[str, ...] = (
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
    "dashscope-us.aliyuncs.com",
)

#: DeepSeek official hosts (same exact-or-subdomain semantics).
DEEPSEEK_HOSTS: tuple[str, ...] = ("deepseek.com",)

#: OpenAI official hosts.
OPENAI_HOSTS: tuple[str, ...] = ("openai.com",)

#: Z.AI (Zhipu GLM) hosts — api.z.ai internationally, bigmodel.cn in China.
ZAI_HOSTS: tuple[str, ...] = ("z.ai", "bigmodel.cn")

#: OpenRouter aggregation gateway.
OPENROUTER_HOSTS: tuple[str, ...] = ("openrouter.ai",)

#: Together AI hosts (openclaw probes api.together.ai / api.together.xyz).
TOGETHER_HOSTS: tuple[str, ...] = ("together.ai", "together.xyz")

#: ModelScope community inference (hosts qwen-family models).
MODELSCOPE_HOSTS: tuple[str, ...] = ("modelscope.cn",)


def _is_dashscope_token_plan(hostname: str) -> bool:
    """DashScope Token-Plan endpoints: ``token-plan.<region>.maas.aliyuncs.com``.

    Both the prefix and the suffix are required (qwen-code's rule) — a
    bare ``maas.aliyuncs.com`` or a ``token-plan.`` host elsewhere must
    not match.
    """
    return hostname.startswith("token-plan.") and hostname.endswith(
        ".maas.aliyuncs.com"
    )


def parse_hostname(base_url: str | None) -> str | None:
    """Return the lowercased hostname of ``base_url``, or ``None``.

    Never raises — an unparseable / relative / empty URL simply means
    "no detection signal", which resolves to the conservative default.
    """
    if not base_url:
        return None
    try:
        host = urlparse(base_url.strip()).hostname
    except (ValueError, TypeError):
        return None
    return host.lower() if host else None


def _matches(hostname: str, hosts: tuple[str, ...]) -> bool:
    return any(
        hostname == host or hostname.endswith("." + host) for host in hosts
    )


def detect_thinking_format(base_url: str | None) -> ThinkingFormat:
    """Auto-detect the thinking dialect from the endpoint hostname.

    Unknown hosts return :attr:`ThinkingFormat.NONE` — the caller must
    not ship any dialect field there (strict endpoints reject unknown
    parameters; lenient ones ignore them).
    """
    hostname = parse_hostname(base_url)
    if not hostname:
        return ThinkingFormat.NONE
    if _matches(hostname, DASHSCOPE_REGIONAL_HOSTS) or _is_dashscope_token_plan(
        hostname
    ):
        return ThinkingFormat.QWEN
    if _matches(hostname, MODELSCOPE_HOSTS):
        return ThinkingFormat.QWEN
    if _matches(hostname, DEEPSEEK_HOSTS):
        return ThinkingFormat.DEEPSEEK
    if _matches(hostname, ZAI_HOSTS):
        return ThinkingFormat.ZAI
    if _matches(hostname, OPENROUTER_HOSTS):
        return ThinkingFormat.OPENROUTER
    if _matches(hostname, TOGETHER_HOSTS):
        return ThinkingFormat.TOGETHER
    if _matches(hostname, OPENAI_HOSTS):
        return ThinkingFormat.OPENAI
    return ThinkingFormat.NONE


def resolve_thinking_format(
    base_url: str | None,
    override: str | None = None,
) -> ThinkingFormat:
    """Apply the user override first, then fall back to detection.

    ``override`` accepts ``None`` / ``""`` / ``"auto"`` for detection,
    or an explicit dialect name (validated). An unrecognised override
    falls back to detection with no exception — the settings layer
    validates on write, so a stale/typo value must not break startup.
    """
    if override:
        normalized = override.strip().lower()
        if normalized != "auto":
            try:
                return ThinkingFormat(normalized)
            except ValueError:
                pass
    return detect_thinking_format(base_url)


def detect_compat(base_url: str | None) -> CompatSpec:
    """Full compat profile for an endpoint (currently one dimension)."""
    return CompatSpec(thinking_format=detect_thinking_format(base_url))
