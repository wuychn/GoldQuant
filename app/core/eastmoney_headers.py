"""东财域名出站请求头：持久化到项目根 `.eastmoney.header`，并对 `requests.Session.request` 打补丁。"""

from __future__ import annotations

import json
from pathlib import Path
from time import sleep
from typing import Any

from requests.sessions import Session

# 与 `app.core.config` 一致：app/core/*.py → 上两级为项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
EASTMONEY_HEADER_FILE = _PROJECT_ROOT / ".eastmoney.header"

_ORIGINAL_SESSION_REQUEST = Session.request
_PATCH_APPLIED = False


def eastmoney_header_file_path() -> Path:
    return EASTMONEY_HEADER_FILE


def load_headers_from_file() -> dict[str, str]:
    """从 `.eastmoney.header` 读取 JSON 数组 [{\"key\",\"value\"},...]，转为 dict。"""
    if not EASTMONEY_HEADER_FILE.is_file():
        return {}
    try:
        raw = EASTMONEY_HEADER_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, list):
            return {}
        out: dict[str, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            k = item.get("key")
            v = item.get("value")
            if k is None or v is None:
                continue
            out[str(k)] = str(v)
        return out
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return {}


def save_headers_to_file(items: list[dict[str, str]]) -> None:
    """整文件覆盖写入 UTF-8 JSON。"""
    EASTMONEY_HEADER_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _patched_session_request(self: Session, method: str, url: str | bytes, **kwargs: Any) -> Any:
    if isinstance(url, bytes):
        url_s = url.decode("utf-8", errors="replace")
    else:
        url_s = str(url)
    extra = load_headers_from_file()
    if extra:
        if "https://push2.eastmoney.com" in url_s or "https://33.push2.eastmoney.com/api/qt/clist/get" in url_s or "https://push2his.eastmoney.com/api/qt/stock/kline/get" in url_s:
            h = dict(kwargs.get("headers") or {})
            h.update(extra)
            kwargs["headers"] = h
    if "eastmoney.com" in url_s:
        sleep(3)
    return _ORIGINAL_SESSION_REQUEST(self, method, url, **kwargs)


def apply_eastmoney_requests_patch() -> None:
    """进程内对 `requests` 打一次补丁；之后访问东财 URL 时会合并 `.eastmoney.header` 中的头。"""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    Session.request = _patched_session_request  # type: ignore[method-assign]
    _PATCH_APPLIED = True
