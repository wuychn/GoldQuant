"""东财行业板块列表（直连 push2 API）。"""

from __future__ import annotations

import logging
import math
import random
import time
from typing import Any

import requests

from quant.data.sources.rate_limit import with_limit

logger = logging.getLogger(__name__)

_EM_INDUSTRY_URL = "https://17.push2.eastmoney.com/api/qt/clist/get"
_EM_INDUSTRY_PARAMS: dict[str, str] = {
    "pn": "1",
    "pz": "100",
    "po": "1",
    "np": "1",
    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": "2",
    "invt": "2",
    "fid": "f3",
    "fs": "m:90 t:2 f:!50",
    "fields": "f12,f14,f3,f2,f4,f5,f6,f7,f8,f9,f10,f20,f104,f105,f128,f140,f141,f207",
}


def _em_get_json(url: str, params: dict[str, str], *, timeout: float) -> dict[str, Any]:
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("东财行业接口返回非 JSON 对象")
    return data


def _fetch_em_industry_board_impl(*, timeout: float = 20, retries: int = 3) -> list[dict[str, Any]]:
    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            params = dict(_EM_INDUSTRY_PARAMS)
            first = _em_get_json(_EM_INDUSTRY_URL, params, timeout=timeout)
            block = first.get("data")
            if not isinstance(block, dict):
                raise ValueError("东财行业 data 缺失")
            diff = block.get("diff") or []
            if not isinstance(diff, list):
                diff = []
            total = int(block.get("total") or len(diff))
            per_page = max(len(diff), 1)
            pages = max(1, math.ceil(total / per_page))

            merged: list[dict[str, Any]] = list(diff)
            for page in range(2, pages + 1):
                time.sleep(random.uniform(0.3, 0.8))
                params["pn"] = str(page)
                page_json = _em_get_json(_EM_INDUSTRY_URL, params, timeout=timeout)
                page_diff = (page_json.get("data") or {}).get("diff") or []
                if isinstance(page_diff, list):
                    merged.extend(page_diff)

            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in merged:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("f12", "")).strip()
                name = str(item.get("f14", "")).strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                rows.append(
                    {
                        "板块代码": code,
                        "板块名称": name,
                        "最新价": item.get("f2"),
                        "涨跌幅": item.get("f3"),
                        "涨跌额": item.get("f4"),
                        "换手率": item.get("f8"),
                        "总市值": item.get("f20"),
                        "上涨家数": item.get("f104"),
                        "下跌家数": item.get("f105"),
                        "领涨股票": item.get("f128"),
                    }
                )
            if not rows:
                raise ValueError("东财行业列表为空")
            return rows
        except Exception as exc:
            last_err = exc
            logger.warning("拉取东财行业 retry=%d err=%s", attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
    try:
        import akshare as ak

        from app.utils.dataframe import dataframe_to_records

        logger.warning("东财直连失败，回退 akshare.stock_board_industry_name_em")
        return dataframe_to_records(ak.stock_board_industry_name_em())
    except Exception:
        pass
    raise RuntimeError("无法拉取东财行业板块列表") from last_err


def fetch_em_industry_board(*, timeout: float = 20, retries: int = 3) -> list[dict[str, Any]]:
    return with_limit(
        "eastmoney",
        lambda: _fetch_em_industry_board_impl(timeout=timeout, retries=retries),
    )
