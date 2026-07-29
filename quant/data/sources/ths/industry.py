"""同花顺行业板块列表。"""

from __future__ import annotations

import logging
import random
import time
from io import StringIO
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

from quant.data.sources.rate_limit import with_limit
from quant.data.sources.ths.hexin import CHROME_USER_AGENT, get_v

logger = logging.getLogger(__name__)

_THS_INDUSTRY_CATE_URL = "https://q.10jqka.com.cn/thshy/detail/code/881272/"
_THS_INDUSTRY_SUMMARY_URL = (
    "http://q.10jqka.com.cn/thshy/index/field/199112/order/desc/page/{page}/ajax/1/"
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


def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, features="lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def _ths_headers() -> dict[str, str]:
    return {
        "User-Agent": CHROME_USER_AGENT,
        "Cookie": f"v={get_v()}",
    }


def _fetch_ths_industry_names_impl(*, timeout: float = 20, retries: int = 3) -> list[dict[str, str]]:
    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            resp = requests.get(_THS_INDUSTRY_CATE_URL, headers=_ths_headers(), timeout=timeout)
            resp.raise_for_status()
            soup = _soup(resp.text)
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


def _fetch_ths_industry_summary_impl(*, timeout: float = 25, retries: int = 3) -> list[dict[str, Any]]:
    last_err: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            headers = _ths_headers()
            first_url = _THS_INDUSTRY_SUMMARY_URL.format(page=1)
            resp = requests.get(first_url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            soup = _soup(resp.text)
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
                out.append({str(k): row[k] for k in row})
            if not out:
                raise ValueError("同花顺行业一览表为空")
            return out
        except Exception as exc:
            last_err = exc
            logger.warning("拉取同花顺行业一览 retry=%d err=%s", attempt + 1, exc)
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("无法拉取同花顺行业一览表") from last_err


def fetch_ths_industry_names(*, timeout: float = 20, retries: int = 3) -> list[dict[str, str]]:
    return with_limit(
        "ths",
        lambda: _fetch_ths_industry_names_impl(timeout=timeout, retries=retries),
    )


def fetch_ths_industry_summary(*, timeout: float = 25, retries: int = 3) -> list[dict[str, Any]]:
    return with_limit(
        "ths",
        lambda: _fetch_ths_industry_summary_impl(timeout=timeout, retries=retries),
    )
