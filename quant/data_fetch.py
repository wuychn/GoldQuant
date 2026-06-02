"""从 FastAPI 拉取五时段量化 JSON，或从 ``data/`` 读取本地 fixture。

endpoint 与 orchestrator 模式一一对应；勿使用已废弃的 /post_market 单路由。
``QUANT_USE_LOCAL_FIXTURE=true`` 时 ``fetch_mode`` 读 ``data/*.json``，不请求 HTTP API。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import requests

from app.core.config import get_settings
from quant.config import BASE_URL

logger = logging.getLogger(__name__)

# mode → API 路径
_ENDPOINTS = {
    "news": "/api/v1/quant/market/news",
    "pre_market": "/api/v1/quant/market/pre_market",
    "during_market": "/api/v1/quant/market/during_market",
    "post_market_lunch": "/api/v1/quant/market/post_market_lunch",
    "post_market_evening": "/api/v1/quant/market/post_market_evening",
}

# mode → data/ 下 fixture 文件名（与 API 响应格式一致：含 code/message/data）
_MODE_FIXTURE_FILES = {
    "news": "news.json",
    "pre_market": "pre_market.json",
    "during_market": "during_market.json",
    "post_market_lunch": "post_market_lunch.json",
    "post_market_evening": "post_market_evening.json",
}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def fixture_path_for_mode(mode: str) -> Path:
    name = _MODE_FIXTURE_FILES.get(mode)
    if not name:
        raise ValueError(f"未知模式: {mode}")
    return _PROJECT_ROOT / "data" / name


def load_mode_fixture(mode: str) -> dict:
    """读取 ``data/<mode>.json``，结构与 ``GET /api/v1/quant/market/...`` 响应一致。"""
    path = fixture_path_for_mode(mode)
    if not path.is_file():
        raise FileNotFoundError(
            f"本地 fixture 不存在: {path}（请导出 API 响应到 data/ 或关闭 QUANT_USE_LOCAL_FIXTURE）"
        )
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"fixture 须为 JSON 对象: {path}")
    return raw


def fetch_mode(mode: str) -> dict:
    """拉取模式数据：``QUANT_USE_LOCAL_FIXTURE=true`` 读本地 JSON，否则请求 FastAPI。"""
    settings = get_settings()
    if settings.QUANT_USE_LOCAL_FIXTURE:
        path = fixture_path_for_mode(mode)
        logger.info("本地 fixture：mode=%s path=%s", mode, path)
        return load_mode_fixture(mode)
    path = _ENDPOINTS.get(mode)
    if not path:
        raise ValueError(f"未知模式: {mode}")
    url = f"{BASE_URL}{path}"
    logger.info("HTTP API：mode=%s url=%s", mode, url)
    resp = requests.get(url, timeout=None)
    resp.raise_for_status()
    return resp.json()


def unwrap_payload(raw: dict) -> dict:
    """剥离 Response 包装 {code, message, data}；news 模式 data 可能为 list。"""
    data = raw.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        return {"news": data}
    return raw
