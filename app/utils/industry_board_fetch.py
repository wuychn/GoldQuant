"""东财 / 同花顺行业板块列表（直连 API，不依赖 akshare 分页/截断）。"""

from __future__ import annotations

import logging
import math
import random
import time
from io import StringIO
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

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

_THS_INDUSTRY_CATE_URL = "https://q.10jqka.com.cn/thshy/detail/code/881272/"
_THS_INDUSTRY_SUMMARY_URL = (
    "http://q.10jqka.com.cn/thshy/index/field/199112/order/desc/page/{page}/ajax/1/"
)
_THS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_SUMMARY_COLUMNS = [
    "序号",
    "板块",
    "涨跌幅",
    "总成交量",
    "总成交额",
    "净流入",
    "上涨家数",
    "下跌家数",
    "均价",
    "领涨股",
    "领涨股-最新价",
    "领涨股-涨跌幅",
]


def _ths_cookie_v() -> str:
    from app.utils.ths_util import get_v

    return get_v()


def _ths_headers() -> dict[str, str]:
    return {
        "User-Agent": _THS_UA,
        "Cookie": f"v={_ths_cookie_v()}",
    }


def _em_get_json(url: str, params: dict[str, str], *, timeout: float) -> dict[str, Any]:
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("东财行业接口返回非 JSON 对象")
    return data


def fetch_em_industry_board(*, timeout: float = 20, retries: int = 3) -> list[dict[str, Any]]:
    """东财沪深行业板块全量列表。

    返回字段：板块代码、板块名称、涨跌幅、最新价 等。
    """
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


def fetch_ths_industry_names(*, timeout: float = 20, retries: int = 3) -> list[dict[str, str]]:
    """同花顺行业名称+代码（thshy 分类页全量）。"""
    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            resp = requests.get(
                _THS_INDUSTRY_CATE_URL,
                headers=_ths_headers(),
                timeout=timeout,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, features="lxml")
            inner = soup.find(name="div", attrs={"class": "cate_inner"})
            if inner is None:
                raise ValueError("未找到同花顺行业分类 cate_inner")
            rows: list[dict[str, str]] = []
            seen: set[str] = set()
            for a in inner.find_all("a"):
                name = (a.text or "").strip()
                href = str(a.get("href", "")).strip()
                code = href.rstrip("/").split("/")[-1] if href else ""
                if not name or name in seen:
                    continue
                seen.add(name)
                rows.append({"name": name, "code": code})
            if not rows:
                raise ValueError("同花顺行业名称为空")
            return rows
        except Exception as exc:
            last_err = exc
            logger.warning("拉取同花顺行业名称 retry=%d err=%s", attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("无法拉取同花顺行业名称") from last_err


def fetch_ths_industry_summary(*, timeout: float = 25, retries: int = 3) -> list[dict[str, Any]]:
    """同花顺行业一览表全量（分页 ajax，非 hyylb 的 Top10）。"""
    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            headers = _ths_headers()
            first_url = _THS_INDUSTRY_SUMMARY_URL.format(page=1)
            resp = requests.get(first_url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, features="lxml")
            page_info = soup.find(name="span", attrs={"class": "page_info"})
            if page_info is None or not page_info.text:
                raise ValueError("未找到同花顺行业分页信息")
            page_num = int(str(page_info.text).split("/")[-1].strip())

            frames: list[pd.DataFrame] = []
            for page in range(1, page_num + 1):
                if page > 1:
                    time.sleep(random.uniform(0.2, 0.6))
                    url = _THS_INDUSTRY_SUMMARY_URL.format(page=page)
                    resp = requests.get(url, headers=headers, timeout=timeout)
                    resp.raise_for_status()
                table = pd.read_html(StringIO(resp.text))[0]
                frames.append(table)

            big = pd.concat(frames, ignore_index=True)
            big.columns = _SUMMARY_COLUMNS[: len(big.columns)]
            records = big.to_dict(orient="records")
            out: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in records:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("板块", "")).strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                cleaned = {str(k): row[k] for k in row}
                out.append(cleaned)
            if not out:
                raise ValueError("同花顺行业一览表为空")
            return out
        except Exception as exc:
            last_err = exc
            logger.warning("拉取同花顺行业一览 retry=%d err=%s", attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("无法拉取同花顺行业一览表") from last_err
