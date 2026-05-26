"""从 FastAPI 拉取五时段量化 JSON。

endpoint 与 orchestrator 模式一一对应；勿使用已废弃的 /post_market 单路由。
"""

from __future__ import annotations

import requests

from quant.config import BASE_URL

# mode → API 路径
_ENDPOINTS = {
    "news": "/api/v1/quant/market/news",
    "pre_market": "/api/v1/quant/market/pre_market",
    "during_market": "/api/v1/quant/market/during_market",
    "post_market_lunch": "/api/v1/quant/market/post_market_lunch",
    "post_market_evening": "/api/v1/quant/market/post_market_evening",
}


def fetch_mode(mode: str) -> dict:
    path = _ENDPOINTS.get(mode)
    if not path:
        raise ValueError(f"未知模式: {mode}")
    resp = requests.get(f"{BASE_URL}{path}", timeout=600)
    resp.raise_for_status()
    return resp.json()


def unwrap_payload(raw: dict) -> dict:
    """剥离 Response 包装 {code, message, data}；news 模式 data 可能为 list。"""
    data = raw.get("data")
    return data if isinstance(data, dict) else raw
