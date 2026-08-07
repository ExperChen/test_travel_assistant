"""测试共用 fixture。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from app.config import Settings, settings
from app.core.cache import cache
from app.providers.amap_client import AmapClient
from app.providers.serpapi_client import SerpApiClient

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clean_cache():
    """缓存是全局的，不清会让用例之间互相污染。"""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def cfg() -> Settings:
    """不读 .env —— 测试不该依赖开发机上的真实 key，也不该有真实重试等待。"""
    return Settings(
        _env_file=None,
        serpapi_key="test-serp-key",
        amap_key="test-amap-key",
        retry_delays_s=(0.0, 0.0),
    )


@pytest.fixture
def serp(cfg: Settings) -> SerpApiClient:
    return SerpApiClient(config=cfg)


@pytest.fixture
def amap(cfg: Settings) -> AmapClient:
    return AmapClient(config=cfg)


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """测试一律不去真的调 Gemini。

    需要验证 LLM 行为的用例显式注入 `llm=FakeLLM()`——那条路径不受这个开关影响。
    """
    monkeypatch.setattr(settings, "llm_enabled", False)


@pytest.fixture(autouse=True)
def _isolated_clients(cfg: Settings):
    """把全局单例换成测试配置的客户端。

    否则图里的节点会去 new 一个读真实 .env 的客户端——测试就依赖开发机环境了。
    """
    from app.tools import registry

    registry.override_clients(serpapi=SerpApiClient(config=cfg), amap=AmapClient(config=cfg))
    yield
    registry.reset_clients()


@pytest.fixture
def load_fixture() -> Callable[[str], dict]:
    """读取 docs 里摘录的真实响应快照。"""

    def _load(name: str) -> dict:
        return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))

    return _load
