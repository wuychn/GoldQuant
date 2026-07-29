"""从 service 直拉五时段量化 JSON，或从 ``data/`` 读取本地 fixture。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from common.progress_log import log_progress, log_progress_done

logger = logging.getLogger(__name__)

_MODE_FIXTURE_FILES = {
    "news": "news.json",
    "pre_market": "pre_market.json",
    "during_market": "during_market.json",
    "post_market_lunch": "post_market_lunch.json",
    "post_market_evening": "post_market_evening.json",
}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def fixture_mode() -> bool:
    from common.config import get_settings

    return bool(get_settings().QUANT_USE_LOCAL_FIXTURE)


def fixture_path_for_mode(mode: str) -> Path:
    name = _MODE_FIXTURE_FILES.get(mode)
    if not name:
        raise ValueError(f"未知模式: {mode}")
    return _PROJECT_ROOT / "data" / name


def load_mode_fixture(mode: str) -> dict:
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
    """直调 service 构建 payload（不经 HTTP）；fixture 模式读本地 JSON。"""
    if fixture_mode():
        path = fixture_path_for_mode(mode)
        log_progress(mode, "读取本地 fixture", detail=str(path))
        return load_mode_fixture(mode)

    from common.config import get_settings
    from quant.services.market.payload import build_mode_payload_async

    log_progress(mode, "直调 service 构建 payload")
    import asyncio

    settings = get_settings()
    br = asyncio.run(build_mode_payload_async(mode, settings))
    data = br.payload
    log_progress_done(mode, "service payload 完成")
    return {"code": 0, "message": "ok", "data": data}


def unwrap_payload(raw: dict) -> dict:
    from common.testing.trim import trim_quant_payload

    data = raw.get("data")
    if isinstance(data, dict):
        return trim_quant_payload(data)
    if isinstance(data, list):
        return trim_quant_payload({"news": data})
    return trim_quant_payload(raw) if isinstance(raw, dict) else raw
