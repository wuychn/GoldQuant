"""应用配置（支持环境变量与 `.env` 文件）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# `app/core/config.py` → 上两级为项目根（含 `.env`），避免依赖进程 cwd
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _PROJECT_ROOT / ".env"

# 量化定时任务默认「盘中」触发时点（comma HH:MM，可通过 `GOLDQUANT_QUANT_SCHED_DURING_MARKET_TIMES` 覆盖）
_QUANT_SCHED_DEFAULT_DURING_TIMES = (
    "09:37,09:47,09:57,10:07,10:17,10:27,10:37,10:47,10:57,"
    "11:07,11:17,11:27,13:07,13:17,13:27,13:37,13:47,13:57,"
    "14:07,14:17,14:27,14:37,14:47,14:57"
)


def _dotenv_get(env_path: Path, key: str) -> str | None:
    """从项目根 ``.env`` 读取 ``KEY=value``（单行；去除引用号）。"""
    if not env_path.is_file():
        return None
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return None
    needle = f"{key}="
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(needle):
            val = line[len(needle) :].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            return val
    return None


def _parse_bool_env(value: str | bool | None) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return None


def _env_plain_or_prefixed(plain: str, prefixed: str, *, env_file: Path) -> str | None:
    """优先进程环境变量，其次 ``.env``；支持无前缀（``LLM_*``）与 ``GOLDQUANT_LLM_*``。"""
    import os

    for name in (plain, prefixed):
        v = os.environ.get(name)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    for name in (plain, prefixed):
        dv = _dotenv_get(env_file, name)
        if dv is not None and dv.strip() != "":
            return dv.strip()
    return None


class Settings(BaseSettings):
    """全局配置，可通过环境变量覆盖（前缀 `GOLDQUANT_`）。"""

    model_config = SettingsConfigDict(
        # 使用项目根目录下的 `.env`，与从哪一级目录启动 `python -m app` / uvicorn 无关
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix="GOLDQUANT_",
        extra="ignore",
    )

    # 应用元信息
    PROJECT_NAME: str = "GoldQuant Data API"
    PROJECT_DESCRIPTION: str = (
        "面向 OpenClaw / 量化决策辅助的股票热度与资讯数据服务。"
        "底层数据来自 AKShare 及同花顺公开接口。"
    )
    VERSION: str = "0.1.0"
    ENV: str = "local"

    # API
    API_V1_STR: str = "/api/v1"

    # Uvicorn（`python -m app` 时使用）
    HOST: str = "0.0.0.0"
    PORT: int = 8085
    UVICORN_RELOAD: bool = True

    #: 是否在启动 FastAPI 时挂载量化流水线定时任务（APScheduler，上海时区等见下项）。
    #: 注意：勿在多 worker（`uvicorn --workers N`）下每台进程启用，否则会重复执行任务。
    QUANT_SCHEDULER_ENABLED: bool = True
    #: IANA 时区名，决定 Cron 触发本地钟点（A 股建议 `Asia/Shanghai`）。
    QUANT_SCHED_TIMEZONE: str = "Asia/Shanghai"
    #: 新闻任务：在哪些「整点小时」执行（逗号分隔 0–23），配合 `QUANT_SCHED_NEWS_MINUTE`。
    QUANT_SCHED_NEWS_HOURS: str = "7,8,9,12,16,17,18,19,20,22,23"
    QUANT_SCHED_NEWS_MINUTE: int = Field(default=0, ge=0, le=59)
    #: 盘前（`pre_market`），仅工作日历命中时才会真正拉起子进程。
    QUANT_SCHED_PRE_MARKET_TIME: str = "09:25"
    #: 盘中（`during_market`）多个触发点，`HH:MM` 逗号分隔。
    QUANT_SCHED_DURING_MARKET_TIMES: str = _QUANT_SCHED_DEFAULT_DURING_TIMES
    #: 午间复盘（`post_market_lunch`），仅交易日（`is_real_workday_cn`）执行。
    QUANT_SCHED_POST_MARKET_LUNCH_TIME: str = "11:50"
    #: 晚间复盘（`post_market_evening`）；工作日当晚 + 假日前一夜（详见 orchestrator）。
    QUANT_SCHED_POST_MARKET_EVENING_TIME: str = "20:10"
    #: 错过触发窗口后的仍可执行宽限（秒）。
    QUANT_SCHED_MISFIRE_GRACE_SEC: int = Field(default=600, ge=60, le=86400)

    # CORS：逗号分隔的源列表，或单独一个 `*` 表示全部（此时不可与凭证共用）
    CORS_ORIGINS: str = "*"
    CORS_ALLOW_CREDENTIALS: bool = False

    # HTTP 客户端（同花顺等直连）
    HTTP_CLIENT_TIMEOUT: float = 30.0
    THS_DEFAULT_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # 出站 HTTP 代理（AKShare / requests / httpx 均会遵循进程环境变量或显式参数）
    PROXY_ENABLED: bool = False
    #: 同时作为 HTTP、HTTPS 默认代理，例如 http://127.0.0.1:7890
    PROXY_URL: str | None = None
    #: 若需区分协议，可单独指定（优先级高于 PROXY_URL）
    PROXY_HTTP_URL: str | None = None
    PROXY_HTTPS_URL: str | None = None
    #: 不走代理的地址列表（逗号分隔），常见内网与本机
    PROXY_NO_PROXY: str = "localhost,127.0.0.1"

    #: 是否将盘前/盘中/盘后聚合结果写入本地归档（快照 + 合并日线 + 指标）
    QUANT_ARCHIVE_ENABLED: bool = True
    #: 归档根目录；未设置时默认为用户目录下 ``~/.quant/archive``
    QUANT_ARCHIVE_DIR: str | None = None
    #: 某股票本地尚无日线归档时，日线全量拉取的起始日期（``YYYYMMDD``，可含 ``-``）
    QUANT_HIST_FULL_START_DATE: str = "19900101"
    #: 本地最后一根日线已是「今天」时，向前重叠拉取的交易日数（复权修正、同日多次刷新）；**补缺**时用「末根次日→今天」，不依赖本项。
    QUANT_HIST_INCREMENTAL_TRADE_DAYS: int = 5
    #: 盘前聚合是否拉取东财 A 股全市场行情表 ``stock_zh_a_spot_em`` 再按代码筛选（数据量大、易触发源站限流；默认关闭）。需要「盘前实时快照」时设 ``GOLDQUANT_QUANT_SPOT_EM_FULL_TABLE=true``。
    QUANT_SPOT_EM_FULL_TABLE: bool = False
    #: 盘前/盘中/盘后接口里「历史行情」日线最多返回条数（从最新往前截），减轻模型上下文；完整 K 线仍在本地归档。
    QUANT_HIST_RESPONSE_MAX_BARS: int = Field(default=48, ge=1, le=4000)

    #: 测试阶段：为 true 时人气榜/涨停统计/盘口异动仅处理前 3 条（仍走实时接口）。
    QUANT_TEST_PHASE: bool = False
    #: 本地数据：为 true 时 ``python -m quant`` 从 ``data/*.json`` 读数，不请求 FastAPI。
    QUANT_USE_LOCAL_FIXTURE: bool = False

    def quant_hot_list_limit(self) -> int:
        """同花顺人气榜处理条数上限。"""
        return 3 if self.QUANT_TEST_PHASE else 30

    def quant_bulk_row_limit(self) -> int | None:
        """涨停统计、盘口异动处理条数上限；``None`` 表示全量。"""
        return 3 if self.QUANT_TEST_PHASE else None

    # 飞书配置
    FEISHU_APP_ID: str | None = None
    FEISHU_APP_SECRET: str | None = None
    FEISHU_USER_ID: str | None = None

    #: 全局 LLM：字段名为 ``LLM_*``；``.env`` 可直接写 ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``（无前缀），亦可写 ``GOLDQUANT_LLM_*``（与旧习惯兼容）。``python main.py`` 与 FastAPI 共用。
    LLM_API_KEY: str | None = None
    LLM_BASE_URL: str = "https://api.minimaxi.com/anthropic"
    LLM_MODEL: str = "MiniMax-M2.7"
    #: ``openai`` → ``/chat/completions``；``anthropic`` → ``/v1/messages``。
    LLM_API_FORMAT: str = "openai"
    #: HTTPS 是否校验证书；内网/自签证书网关可设 ``false``。
    LLM_VERIFY_SSL: bool = False
    #: 是否读取系统 ``HTTP_PROXY`` 等环境变量（企业代理干扰时设 ``false``）。
    LLM_TRUST_ENV_PROXY: bool = False
    LLM_TIMEOUT_SEC: float = Field(default=600.0, ge=30.0, le=3600.0)
    LLM_USE_APP_PROXY: bool = False

    @field_validator(
        "QUANT_TEST_PHASE",
        "QUANT_USE_LOCAL_FIXTURE",
        "LLM_VERIFY_SSL",
        "LLM_TRUST_ENV_PROXY",
        "LLM_USE_APP_PROXY",
        mode="before",
    )
    @classmethod
    def coerce_bool_flags(cls, v: object) -> bool:
        if isinstance(v, bool):
            return v
        parsed = _parse_bool_env(str(v) if v is not None else None)
        if parsed is not None:
            return parsed
        raise ValueError(f"布尔配置无法解析: {v!r}")

    @model_validator(mode="after")
    def merge_llm_plain_env_names(self) -> Settings:
        """兼容 ``.env`` 中无前缀的 ``LLM_*`` / ``FEISHU_*`` / 量化布尔开关。"""
        ak = self.LLM_API_KEY
        if not ak:
            ak = _env_plain_or_prefixed("LLM_API_KEY", "GOLDQUANT_LLM_API_KEY", env_file=_ENV_FILE)
        bu_o = _env_plain_or_prefixed("LLM_BASE_URL", "GOLDQUANT_LLM_BASE_URL", env_file=_ENV_FILE)
        bu = bu_o if bu_o else self.LLM_BASE_URL
        md_o = _env_plain_or_prefixed("LLM_MODEL", "GOLDQUANT_LLM_MODEL", env_file=_ENV_FILE)
        md = md_o if md_o else self.LLM_MODEL
        fmt_o = _env_plain_or_prefixed("LLM_API_FORMAT", "GOLDQUANT_LLM_API_FORMAT", env_file=_ENV_FILE)
        fmt = fmt_o if fmt_o else self.LLM_API_FORMAT
        object.__setattr__(self, "LLM_API_KEY", ak)
        object.__setattr__(self, "LLM_BASE_URL", bu)
        object.__setattr__(self, "LLM_MODEL", md)
        object.__setattr__(self, "LLM_API_FORMAT", fmt)

        # 飞书配置（支持无前缀 FEISHU_*）
        for field_name in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_USER_ID"):
            val = getattr(self, field_name, None)
            if not val:
                val = _env_plain_or_prefixed(field_name, f"GOLDQUANT_{field_name}", env_file=_ENV_FILE)
                if val:
                    object.__setattr__(self, field_name, val)

        for plain, prefixed, attr in (
            ("QUANT_TEST_PHASE", "GOLDQUANT_QUANT_TEST_PHASE", "QUANT_TEST_PHASE"),
            ("QUANT_USE_LOCAL_FIXTURE", "GOLDQUANT_QUANT_USE_LOCAL_FIXTURE", "QUANT_USE_LOCAL_FIXTURE"),
            ("LLM_VERIFY_SSL", "GOLDQUANT_LLM_VERIFY_SSL", "LLM_VERIFY_SSL"),
            ("LLM_TRUST_ENV_PROXY", "GOLDQUANT_LLM_TRUST_ENV_PROXY", "LLM_TRUST_ENV_PROXY"),
            ("LLM_USE_APP_PROXY", "GOLDQUANT_LLM_USE_APP_PROXY", "LLM_USE_APP_PROXY"),
        ):
            raw = _env_plain_or_prefixed(plain, prefixed, env_file=_ENV_FILE)
            if raw is not None:
                val = _parse_bool_env(raw)
                if val is not None:
                    object.__setattr__(self, attr, val)
        return self

    @field_validator("CORS_ORIGINS")
    @classmethod
    def strip_cors_origins(cls, v: str) -> str:
        return v.strip()

    def cors_allow_origins(self) -> list[str]:
        """解析为 Starlette CORS 所需的源列表。"""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [p.strip() for p in self.CORS_ORIGINS.split(",") if p.strip()]

    def cors_effective_credentials(self) -> bool:
        """与 `allow_origins=['*']` 同时使用时浏览器规范要求 credentials 为 False。"""
        if self.cors_allow_origins() == ["*"]:
            return False
        return self.CORS_ALLOW_CREDENTIALS

    def proxy_http_effective(self) -> str | None:
        if not self.PROXY_ENABLED:
            return None
        u = (self.PROXY_HTTP_URL or self.PROXY_URL or "").strip()
        return u or None

    def proxy_https_effective(self) -> str | None:
        if not self.PROXY_ENABLED:
            return None
        u = (self.PROXY_HTTPS_URL or self.PROXY_URL or "").strip()
        return u or None

    def httpx_proxy_url(self) -> str | None:
        """httpx 单 `proxy` 参数：优先 HTTPS 侧（与常见 Clash 一致）。"""
        return self.proxy_https_effective() or self.proxy_http_effective()

    @model_validator(mode="after")
    def proxy_requires_url(self) -> Settings:
        if not self.PROXY_ENABLED:
            return self
        if self.proxy_http_effective() or self.proxy_https_effective():
            return self
        raise ValueError(
            "GOLDQUANT_PROXY_ENABLED=true 时需设置 GOLDQUANT_PROXY_URL "
            "或 GOLDQUANT_PROXY_HTTP_URL / GOLDQUANT_PROXY_HTTPS_URL"
        )


@lru_cache
def get_settings() -> Settings:
    """单例式设置（测试时可使用 `get_settings.cache_clear()`）。"""
    return Settings()
