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
from quant.progress_log import log_progress, log_progress_done

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
        log_progress(mode, "读取本地 fixture", detail=str(path))
        return load_mode_fixture(mode)
    path = _ENDPOINTS.get(mode)
    if not path:
        raise ValueError(f"未知模式: {mode}")
    url = f"{BASE_URL}{path}"
    log_progress(mode, "请求 HTTP API", detail=url)
    resp = requests.get(url, timeout=None)
    if not resp.ok:
        from app.utils.error_log import format_http_response_body

        body_preview = format_http_response_body(resp.text)
        log_progress(mode, "HTTP 响应错误", detail=f"status={resp.status_code}\n{body_preview}")
    resp.raise_for_status()
    log_progress_done(mode, "HTTP 响应成功")
    return resp.json()


def unwrap_payload(raw: dict) -> dict:
    """剥离 Response 包装 {code, message, data}；news 模式 data 可能为 list。"""
    from app.utils.quant_test_trim import trim_quant_payload

    data = raw.get("data")
    if isinstance(data, dict):
        return trim_quant_payload(data)
    if isinstance(data, list):
        return trim_quant_payload({"news": data})
    return trim_quant_payload(raw) if isinstance(raw, dict) else raw
