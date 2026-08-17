"""Endpoint detection: base_url → thinking dialect.

Locks the three-tier resolution rule (explicit override > URL detection
> conservative NONE) and the hostname-parsing safety properties: exact
or parent-domain match only, path components never influence detection.
"""

import pytest

from chaos_agent.llm import (
    ThinkingFormat,
    detect_compat,
    detect_thinking_format,
    parse_hostname,
    resolve_thinking_format,
)

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class TestParseHostname:
    def test_extracts_lowercased_host(self):
        assert parse_hostname("https://DashScope.AliyunCS.com/v1") == (
            "dashscope.aliyuncs.com"
        )

    @pytest.mark.parametrize("bad", [None, "", "   ", "not a url", "://"])
    def test_unparseable_returns_none(self, bad):
        assert parse_hostname(bad) is None


class TestDetectThinkingFormat:
    @pytest.mark.parametrize(
        "url",
        [
            DASHSCOPE_URL,
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "https://dashscope-us.aliyuncs.com/v1",
        ],
    )
    def test_dashscope_regional_hosts(self, url):
        assert detect_thinking_format(url) is ThinkingFormat.QWEN

    def test_dashscope_subdomain_matches(self):
        assert (
            detect_thinking_format("https://proxy.dashscope.aliyuncs.com/v1")
            is ThinkingFormat.QWEN
        )

    def test_dashscope_token_plan_endpoint_matches(self):
        """Token-Plan endpoints need BOTH the ``token-plan.`` prefix and
        the ``.maas.aliyuncs.com`` suffix (qwen-code's rule)."""
        assert (
            detect_thinking_format(
                "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
            )
            is ThinkingFormat.QWEN
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://token-plan.evil.com/v1",
            "https://maas.aliyuncs.com/v1",
            "https://token-plan.maas.aliyuncs.com.evil.example/v1",
        ],
    )
    def test_token_plan_lookalikes_do_not_match(self, url):
        assert detect_thinking_format(url) is ThinkingFormat.NONE

    @pytest.mark.parametrize(
        "url",
        [
            "https://modelscope.cn/api/v1",
            "https://api.modelscope.cn/v1",
        ],
    )
    def test_modelscope_hosts(self, url):
        assert detect_thinking_format(url) is ThinkingFormat.QWEN

    def test_modelscope_lookalike_does_not_match(self):
        assert (
            detect_thinking_format("https://modelscope.cn.evil.example/v1")
            is ThinkingFormat.NONE
        )

    def test_deepseek_host(self):
        assert (
            detect_thinking_format("https://api.deepseek.com/v1")
            is ThinkingFormat.DEEPSEEK
        )

    def test_openai_host(self):
        assert (
            detect_thinking_format("https://api.openai.com/v1")
            is ThinkingFormat.OPENAI
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.z.ai/api/paas/v4",
            "https://open.bigmodel.cn/api/paas/v4",
        ],
    )
    def test_zai_hosts(self, url):
        assert detect_thinking_format(url) is ThinkingFormat.ZAI

    def test_zai_lookalike_does_not_match(self):
        assert detect_thinking_format("https://evilz.ai/v1") is ThinkingFormat.NONE

    @pytest.mark.parametrize(
        "url",
        [
            "https://openrouter.ai/api/v1",
            "https://api.together.ai/v1",
            "https://api.together.xyz/v1",
        ],
    )
    def test_aggregator_hosts(self, url):
        expected = (
            ThinkingFormat.OPENROUTER
            if "openrouter" in url
            else ThinkingFormat.TOGETHER
        )
        assert detect_thinking_format(url) is expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://my-gateway.internal/v1",
            "https://vllm.corp.example:8000/v1",
            None,
            "",
        ],
    )
    def test_unknown_endpoints_get_none(self, url):
        assert detect_thinking_format(url) is ThinkingFormat.NONE

    def test_path_spoofing_does_not_match(self):
        """Hostnames are parsed, never regex-matched against the full
        URL — a dashscope-looking path segment on a foreign host must
        not enable the qwen dialect."""
        assert (
            detect_thinking_format(
                "https://evil.example/dashscope.aliyuncs.com/v1"
            )
            is ThinkingFormat.NONE
        )

    def test_lookalike_hostname_does_not_match(self):
        assert (
            detect_thinking_format("https://dashscope.aliyuncs.com.evil.example/v1")
            is ThinkingFormat.NONE
        )

    def test_detect_compat_wraps_format(self):
        assert detect_compat(DASHSCOPE_URL).thinking_format is ThinkingFormat.QWEN
        assert (
            detect_compat("https://x.example/v1").thinking_format
            is ThinkingFormat.NONE
        )


class TestResolveThinkingFormat:
    def test_auto_falls_back_to_detection(self):
        assert (
            resolve_thinking_format(DASHSCOPE_URL, "auto") is ThinkingFormat.QWEN
        )

    @pytest.mark.parametrize("override", [None, "", "auto", "  AUTO  "])
    def test_unset_overrides_detect(self, override):
        assert (
            resolve_thinking_format(DASHSCOPE_URL, override)
            is ThinkingFormat.QWEN
        )

    def test_explicit_override_wins_over_url(self):
        assert (
            resolve_thinking_format(DASHSCOPE_URL, "none") is ThinkingFormat.NONE
        )
        assert (
            resolve_thinking_format("https://x.example/v1", "qwen")
            is ThinkingFormat.QWEN
        )

    def test_override_is_case_insensitive(self):
        assert (
            resolve_thinking_format(None, "DeepSeek") is ThinkingFormat.DEEPSEEK
        )

    def test_garbage_override_falls_back_to_detection(self):
        # A stale/typo value must not break startup; detection takes over.
        assert (
            resolve_thinking_format(DASHSCOPE_URL, "qwn") is ThinkingFormat.QWEN
        )
        assert (
            resolve_thinking_format("https://x.example/v1", "qwn")
            is ThinkingFormat.NONE
        )
