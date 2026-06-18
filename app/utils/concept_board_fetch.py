"""同花顺概念板块资金流（直连 THS，避免 akshare 分页解析偶发失败）。"""

from __future__ import annotations

import logging
from io import StringIO
from typing import Any

import pandas as pd
import requests
from bs4 import BeautifulSoup

from app.utils.dataframe import dataframe_to_records

logger = logging.getLogger(__name__)

_THS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_COUNT_URL = (
    "http://data.10jqka.com.cn/funds/gnzjl/field/tradezdf/order/desc/ajax/1/free/1/"
)
_URL_BY_SYMBOL: dict[str, str] = {
    "3日排行": (
        "http://data.10jqka.com.cn/funds/gnzjl/board/3/field/tradezdf/order/desc/page/{page}/ajax/1/free/1/"
    ),
    "5日排行": (
        "http://data.10jqka.com.cn/funds/gnzjl/board/5/field/tradezdf/order/desc/page/{page}/ajax/1/free/1/"
    ),
    "10日排行": (
        "http://data.10jqka.com.cn/funds/gnzjl/board/10/field/tradezdf/order/desc/page/{page}/ajax/1/free/1/"
    ),
    "20日排行": (
        "http://data.10jqka.com.cn/funds/gnzjl/board/20/field/tradezdf/order/desc/page/{page}/ajax/1/free/1/"
    ),
}
_DEFAULT_PAGE_URL = (
    "http://data.10jqka.com.cn/funds/gnzjl/field/tradezdf/order/desc/page/{page}/ajax/1/free/1/"
)


def _ths_headers() -> dict[str, str]:
    from app.utils.ths_util import get_v

    return {
        "Accept": "text/html, */*; q=0.01",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "hexin-v": get_v(),
        "Host": "data.10jqka.com.cn",
        "Pragma": "no-cache",
        "Referer": "http://data.10jqka.com.cn/funds/gnzjl/",
        "User-Agent": _THS_UA,
        "X-Requested-With": "XMLHttpRequest",
    }


def _page_count(first_html: str) -> int:
    soup = BeautifulSoup(first_html, features="lxml")
    page_info = soup.find(name="span", attrs={"class": "page_info"})
    if page_info and page_info.text and "/" in page_info.text:
        try:
            return max(1, int(page_info.text.split("/")[1].strip()))
        except ValueError:
            pass
    return 1


def _normalize_concept_fund_flow_df(df: pd.DataFrame, *, symbol: str) -> pd.DataFrame:
    out = df.copy()
    if "序号" in out.columns:
        out = out.drop(columns=["序号"])
    out.reset_index(drop=True, inplace=True)
    out.insert(0, "序号", range(1, len(out) + 1))

    if symbol == "即时":
        rename_map = {
            "涨跌幅": "行业-涨跌幅",
            "涨跌幅.1": "领涨股-涨跌幅",
            "当前价(元)": "当前价",
        }
        out.rename(columns={k: v for k, v in rename_map.items() if k in out.columns}, inplace=True)
        for col in ("行业-涨跌幅", "领涨股-涨跌幅"):
            if col in out.columns:
                out[col] = (
                    out[col]
                    .astype(str)
                    .str.strip()
                    .str.replace("%", "", regex=False)
                )
                out[col] = pd.to_numeric(out[col], errors="coerce")
        expected = [
            "序号",
            "行业",
            "行业指数",
            "行业-涨跌幅",
            "流入资金",
            "流出资金",
            "净额",
            "公司家数",
            "领涨股",
            "领涨股-涨跌幅",
            "当前价",
        ]
        for name in expected:
            if name not in out.columns:
                out[name] = None
        return out[expected]

    out.columns = [
        "序号",
        "行业",
        "公司家数",
        "行业指数",
        "阶段涨跌幅",
        "流入资金",
        "流出资金",
        "净额",
    ]
    return out


def fetch_ths_concept_fund_flow(symbol: str = "即时") -> list[dict[str, Any]]:
    """拉取同花顺概念资金流全量并规范化为与 akshare 一致的字段。"""
    headers = _ths_headers()
    count_resp = requests.get(_COUNT_URL, headers=headers, timeout=30)
    count_resp.raise_for_status()
    page_num = _page_count(count_resp.text)

    url_tmpl = _URL_BY_SYMBOL.get(symbol, _DEFAULT_PAGE_URL)
    big_df = pd.DataFrame()
    for page in range(1, page_num + 1):
        page_headers = _ths_headers()
        resp = requests.get(url_tmpl.format(page=page), headers=page_headers, timeout=30)
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
        if not tables:
            logger.warning("概念资金流第 %s 页无表格", page)
            continue
        big_df = pd.concat([big_df, tables[0]], ignore_index=True)

    if big_df.empty:
        raise ValueError("概念资金流返回空表")

    # 去掉列名中的单位后缀，与 akshare 字段对齐
    big_df.columns = [
        str(c).split("(")[0].split("（")[0].strip() for c in big_df.columns
    ]
    norm = _normalize_concept_fund_flow_df(big_df, symbol=symbol)
    return dataframe_to_records(norm)
