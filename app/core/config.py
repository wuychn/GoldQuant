"""应用配置（支持环境变量与 `.env` 文件）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# `app/core/config.py` → 上两级为项目根（含 `.env`），避免依赖进程 cwd
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _PROJECT_ROOT / ".env"


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
    PORT: int = 8000
    UVICORN_RELOAD: bool = True

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
