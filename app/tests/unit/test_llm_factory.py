"""LLM 客户端工厂测试。

不发任何真实请求——只验证"按配置选对了 SDK、参数传对了"。
"""

from __future__ import annotations

import pytest

from app.config import Settings, settings
from app.providers import llm as factory


@pytest.fixture(autouse=True)
def _fresh_cache():
    factory.reset_llm()
    yield
    factory.reset_llm()


class TestOpenAICompatible:
    def test_builds_a_client_pointed_at_the_configured_base_url(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(settings, "llm_base_url", "https://api.deepseek.com")
        monkeypatch.setattr(settings, "llm_api_key", "sk-test")
        monkeypatch.setattr(settings, "llm_model", "deepseek-v4-flash")

        client = factory.get_llm()

        assert client.model_name == "deepseek-v4-flash"
        assert str(client.openai_api_base).rstrip("/") == "https://api.deepseek.com"

    def test_switching_vendor_is_pure_configuration(self, monkeypatch):
        # 换供应商只改 base_url + model + key，代码一行不动
        monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(settings, "llm_api_key", "sk-test")
        monkeypatch.setattr(settings, "llm_base_url", "https://open.bigmodel.cn/api/paas/v4")
        monkeypatch.setattr(settings, "llm_model", "glm-4")

        client = factory.get_llm()

        assert client.model_name == "glm-4"
        assert "bigmodel.cn" in str(client.openai_api_base)

    def test_missing_key_fails_fast_with_the_variable_name(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(settings, "llm_api_key", "")

        # 与其等 HTTP 401，不如在构造时就把缺哪个环境变量说清楚
        with pytest.raises(RuntimeError, match="LLM_API_KEY"):
            factory.get_llm()

    def test_temperature_override(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(settings, "llm_api_key", "sk-test")

        assert factory.get_llm(temperature=0.9).temperature == 0.9

    def test_retries_are_left_to_the_caller(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(settings, "llm_api_key", "sk-test")

        # SDK 内部多轮重试只会让用户干等；失败直接退模板更快也更可预期
        assert factory.get_llm().max_retries <= 1


class TestGeminiBranch:
    def test_missing_google_key_fails_fast(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "gemini")
        monkeypatch.setattr(settings, "google_api_key", "")

        with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
            factory.get_llm()


class TestDefaults:
    def test_ships_pointing_at_an_endpoint_reachable_from_mainland_china(self):
        # Gemini 会按服务端 IP 归属地拒绝，默认值不该踩这个坑
        fresh = Settings(_env_file=None)
        assert fresh.llm_provider == "openai_compatible"
        assert fresh.llm_base_url.startswith("https://")

    def test_instances_are_cached(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(settings, "llm_api_key", "sk-test")

        assert factory.get_llm() is factory.get_llm()

    def test_reset_drops_the_cache(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "openai_compatible")
        monkeypatch.setattr(settings, "llm_api_key", "sk-test")

        first = factory.get_llm()
        factory.reset_llm()
        assert factory.get_llm() is not first
