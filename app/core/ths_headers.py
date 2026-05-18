"""同花顺直连请求头：持久化到项目根 `.ths.header`（JSON 数组），URL 命中规则见代码常量。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# 与 `app.core.config` 一致：app/core/*.py → 上两级为项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
THS_HEADER_FILE = _PROJECT_ROOT / ".ths.header"

# 仅当请求 URL 包含下列子串之一时，才合并 `.ths.header` 中的请求头（与本服务直连地址一致，可在此增删）
_THS_HEADER_URL_MARKERS: tuple[str, ...] = (
    "dq.10jqka.com.cn",
    "q.10jqka.com.cn",
)


def ths_header_file_path() -> Path:
    return THS_HEADER_FILE


def load_ths_headers_from_file() -> dict[str, str]:
    """从 `.ths.header` 读取 JSON。

    - 标准格式：与东财一致的数组 `[{\"key\",\"value\"}, ...]`。
    - 兼容旧版：对象 `{\"headers\": [...] , ...}` 时只读取其中的 ``headers`` 数组（忽略其它键）。
    """
    if not THS_HEADER_FILE.is_file():
        return {}
    try:
        raw = THS_HEADER_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        data: Any = json.loads(raw)
        if isinstance(data, list):
            return _headers_list_to_dict(data)
        if isinstance(data, dict):
            h_raw = data.get("headers", data.get("header", []))
            if isinstance(h_raw, list):
                return _headers_list_to_dict(h_raw)
        return {}
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return {}


def _headers_list_to_dict(items: list[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        k = item.get("key")
        v = item.get("value")
        if k is None or v is None:
            continue
        out[str(k)] = str(v)
    return out


def save_ths_headers_to_file(items: list[dict[str, str]]) -> None:
    """整文件覆盖写入 UTF-8 JSON 数组（与 `.eastmoney.header` 形态一致）。"""
    THS_HEADER_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _url_matches_ths_header_injection(url: str) -> bool:
    u = str(url)
    return any(marker in u for marker in _THS_HEADER_URL_MARKERS)


def merge_ths_headers_for_url(url: str, base: dict[str, str]) -> dict[str, str]:
    """若 `url` 命中代码内配置的子串，则将文件中的请求头合并进 `base`（同名键以文件为准）。"""
    extra = load_ths_headers_from_file()
    if not extra:
        return dict(base)
    if not _url_matches_ths_header_injection(url):
        return dict(base)
    merged = dict(base)
    merged.update(extra)
    return merged
