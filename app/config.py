"""应用配置：全部来自环境变量 / .env，任何密钥都不得硬编码。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 密钥 ----
    serpapi_key: str = ""
    amap_key: str = ""
    google_api_key: str = ""
    app_api_key: str = ""
    """本服务对外鉴权用的 key；留空表示关闭鉴权（仅限本地开发）。"""

    # ---- LLM ----
    llm_provider: Literal["openai_compatible", "gemini"] = "openai_compatible"
    """走哪套 SDK。

    `openai_compatible` 覆盖 DeepSeek / 阿里云百炼 / 智谱 / Kimi / 火山方舟 /
    硅基流动 / OpenAI 等一大票厂商——它们的接口都兼容，换供应商只需要改
    `LLM_BASE_URL` + `LLM_MODEL` + `LLM_API_KEY` 三个环境变量，代码一行不动。

    `gemini` 分支保留着，但注意 Google 按服务端 IP 归属地做策略拒绝：
    中国大陆直连会返回 `FAILED_PRECONDITION: User location is not supported`，
    需要挂支持地区的代理。
    """
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-v4-flash"
    llm_temperature: float = 0.2
    llm_timeout_s: float = 30.0
    llm_enabled: bool = True
    """置 false 则跳过 LLM，直接用确定性模板生成行程说明。

    在调不通模型服务的网络环境里，关掉它可以省下每次 30 秒的超时等待——
    模板文案里每个数字都直接取自 Itinerary，内容与 LLM 路径一致。"""

    # ---- 本地化：D1 决策下目的地限中国大陆，故默认全部中国口径 ----
    default_gl: str = "cn"
    default_hl: str = "zh-CN"
    default_currency: str = "CNY"

    serpapi_flights_gl: str = ""
    """航班搜索单独的 `gl`（销售地）。**留空是刻意的，不是漏了。**

    实测 PEK→CTU 2026-09-05/08 同一时刻的对照（只差这一个参数）：

        留空：  ¥4685 3U 8890 直飞 / ¥4685 3U 8900 直飞 / ¥4685 3U 8894 直飞
        gl=cn： ¥4735 PEK→无锡→成都 两段中转（首选！）
                ¥6160 HU 7847 直飞
                ¥None 3U 8894 直飞（价格干脆缺失）

    `gl=cn` 把两段中转排在直飞前面、直飞报价反而更高、还有条目没价格——
    Google 在中国销售地的机票库存覆盖本来就差。所以航班这条链路不跟随
    `default_gl`（酒店那边跟随，那边 gl=cn 是正常的）。

    要试的话设成 "cn" 即可，两种都跑过、都能出结果。"""

    # ---- Provider 端点 ----
    serpapi_base_url: str = "https://serpapi.com/search.json"
    amap_base_url: str = "https://restapi.amap.com"

    # ---- 超时与重试（架构文档 §9.3）----
    http_timeout_s: float = 20.0
    http_connect_timeout_s: float = 5.0
    retry_attempts: int = 3
    retry_delays_s: tuple[float, ...] = (0.5, 1.5, 4.0)
    breaker_failure_threshold: int = 5
    breaker_reset_after_s: float = 60.0

    # ---- 缓存 TTL（秒）----
    cache_ttl_serpapi_s: int = 3600
    """SerpAPI 服务端自带 1h 缓存，本地再挡一层，避免重复请求白扣额度。"""
    cache_ttl_amap_poi_s: int = 86400
    """景点数据变化慢。"""
    cache_ttl_amap_route_s: int = 900
    """路径受路况影响，短 TTL。"""
    cache_max_entries: int = 2048

    # ---- 长期记忆（记忆与追问文档 §5 / §6）----
    memory_enabled: bool = True
    """关掉它，`prompt_parser` 和景点打分都退回没有记忆时的行为。

    记忆是纯增量特性：任何一处失败都只会退化成"这个人没有记忆"，
    绝不能让规划本身失败。"""
    memory_db_path: str = str(BASE_DIR / "data" / "memory.db")
    """SQLite 单文件。上多副本时和 checkpointer 一起迁 Postgres。"""

    # ---- ReAct 参数收集（Flight ReAct Agent 设计文档 §2）----
    react_enabled: bool = True
    """关掉则 `/trips/parse` 退回单次抽取（现有的 `parse_prompt` 行为）。"""
    react_max_steps: int = 6
    """ReAct 循环的硬上限。

    到达上限就带着已收集到的参数收尾，而不是继续烧 token——参数收集本身
    最多用到 2~3 次工具调用（两端机场 + 城市校验），6 步足够容错。"""
    clarify_enabled: bool = True
    """是否在 intake 之后追问关键的缺失字段。"""

    # ---- 业务默认值 ----
    interrupt_timeout_s: int = 600
    """中断问题多久没人回答就用默认值自动恢复。"""
    checkin_buffer_min: int = 60
    predeparture_buffer_min: int = 120
    day_start_hour: int = 9
    day_end_hour: int = 21
    departure_day_start_hour: int = 8

    # ---- 可观测性 ----
    log_level: str = "INFO"
    log_json: bool = True

    # ---- 限流与 CORS ----
    rate_limit_create: str = "10/minute"
    rate_limit_read: str = "60/minute"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    """默认只放本地前端开发端口。绝不用 "*"——带 X-API-Key 的请求配通配来源，
    等于把 key 暴露给任意站点。上线时按实际域名配置。"""

    quota_warn_ratio: float = Field(0.8, ge=0.0, le=1.0)

    # ------------------------------------------------------------------
    def require(self, *names: str) -> None:
        """在真正要用到某个 key 前调用；缺失时立刻报错，而不是等 HTTP 401。"""
        missing = [n for n in names if not getattr(self, n, "")]
        if missing:
            raise RuntimeError(
                "缺少必需的环境变量：" + ", ".join(n.upper() for n in missing) + "。"
                "请复制 .env.example 为 .env 并填入真实值。"
            )

    @property
    def auth_enabled(self) -> bool:
        return bool(self.app_api_key)

    @property
    def active_llm_key(self) -> str:
        """当前 provider 实际会用到的那个 key。

        两个分支读的是不同变量，探活不能只盯着其中一个——否则用 DeepSeek 的
        人会看到"llm: missing_key"，反之亦然。
        """
        return self.google_api_key if self.llm_provider == "gemini" else self.llm_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
