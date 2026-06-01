import asyncio
import json
import secrets
import string
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, urlparse

import akshare as ak
import httpx
import py_mini_racer
from akshare.stock_feature.stock_fund_flow import _get_file_content_ths
from fastapi import HTTPException

from app.api.deps import SettingsDep
from app.utils.common_util import filter_exclude_by_key, format_percent, sort_by_field_and_limit
from app.utils.dataframe import dataframe_to_records
from app.utils.iwencai_hexin_util import get_iwencai_hexin_v

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HTTP_TIMEOUT = 30
_A_SHARE_PREFIXES = ("60", "0", "3")

_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
_SEC_CH_UA = '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"'

_HOT_LIST_URL = (
    "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
    "?stock_type=a&type=hour&list_type={list_type}"
)
_ZDFB_URL = "https://q.10jqka.com.cn/api.php?t=indexflash&"
_STOCK_FUNDS_URL = (
    "https://stockpage.10jqka.com.cn/spService/{symbol}/Funds/realFunds/free/1/"
)

IWENCAI_GET_ROBOT_DATA_URL = (
    "https://www.iwencai.com/unifiedwap/unified-wap/v2/result/get-robot-data"
)
_IWENCAI_ORIGIN = "https://www.iwencai.com"
_IWENCAI_SOURCE = "Ths_iwencai_Xuangu"
_IWENCAI_ADD_INFO = (
    '{"urp":{"scene":1,"company":1,"business":1},'
    '"contentType":"json","searchInfo":true}'
)
_WENCAI_CONCEPT_TABLE_TITLE = "所属概念列表"
_WENCAI_CONCEPT_FIELD = "诊股概念分类名称"

_ths_js_ctx: py_mini_racer.MiniRacer | None = None


# ---------------------------------------------------------------------------
# 10jqka Hexin-V (ths.js)
# ---------------------------------------------------------------------------

def _get_ths_js_ctx() -> py_mini_racer.MiniRacer:
    global _ths_js_ctx
    if _ths_js_ctx is None:
        _ths_js_ctx = py_mini_racer.MiniRacer()
        _ths_js_ctx.eval(_get_file_content_ths("ths.js"))
    return _ths_js_ctx


def get_v() -> str:
    """生成同花顺 Hexin-V（与 cookie ``v`` 值一致）。"""
    return _get_ths_js_ctx().call("v")


# ---------------------------------------------------------------------------
# Cookie helpers (iwencai)
# ---------------------------------------------------------------------------

def _parse_cookie_header(cookie_header: str | None) -> dict[str, str]:
    if not cookie_header:
        return {}
    cookies: dict[str, str] = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        cookies[name.strip()] = value.strip()
    return cookies


def _cookie_header_from_dict(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _generate_other_uid() -> str:
    suffix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(32))
    return f"Ths_iwencai_Xuangu_{suffix}"


def _resolve_iwencai_rsh(cookies: dict[str, str]) -> str:
    """
    问财 form 字段 rsh：与页面 ``getUserid()`` / ``baseAction`` 一致。
    优先 cookie ``userid``，否则 ``other_uid``，都没有则生成匿名 other_uid。
    """
    userid = cookies.get("userid", "").strip()
    if userid and userid not in {"0", ""}:
        return userid
    other_uid = cookies.get("other_uid", "").strip()
    if other_uid:
        return other_uid
    generated = _generate_other_uid()
    cookies["other_uid"] = generated
    return generated


def _build_iwencai_cookie(hexin_v: str, cookies: dict[str, str]) -> str:
    merged = dict(cookies)
    merged["v"] = hexin_v
    if not merged.get("other_uid") and not merged.get("userid"):
        merged["other_uid"] = _generate_other_uid()
    return _cookie_header_from_dict(merged)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _browser_common_headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7,ko;q=0.6",
        "Connection": "keep-alive",
        "Sec-Ch-Ua": _SEC_CH_UA,
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "x-requested-with": "XMLHttpRequest",
    }


async def _request_json(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        client_kw: dict[str, Any] | None = None,
        **kwargs: Any,
) -> Any:
    client_kw = client_kw or {"timeout": _HTTP_TIMEOUT}
    try:
        async with httpx.AsyncClient(**client_kw) as client:
            response = await client.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# 10jqka API
# ---------------------------------------------------------------------------

async def call_ths_api(settings: SettingsDep, url: str) -> Any:
    """直接调用同花顺接口。"""
    headers = {
        "User-Agent": settings.THS_DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }
    # 这里是添加同花顺的请求头，现在同花顺接口的 Cookie 还不知道怎么绕过 TODO
    # headers = merge_ths_headers_for_url(url, headers)
    client_kw: dict[str, Any] = {"timeout": settings.HTTP_CLIENT_TIMEOUT}
    if px := settings.httpx_proxy_url():
        client_kw["proxy"] = px
    return await _request_json("GET", url, headers=headers, client_kw=client_kw)


async def call_ths_api_with_header(url: str) -> Any:
    """直接调用同花顺接口（带 Hexin-V 请求头）。"""
    headers = {
        "User-Agent": _CHROME_USER_AGENT,
        **_browser_common_headers(),
        "Cookie": "keep-alive",
        "Hexin-V": get_v(),
        "Host": "stockpage.10jqka.com.cn",
        "Referer": "https://stockpage.10jqka.com.cn/002580/funds/",
    }
    return await _request_json("GET", url, headers=headers)


# ---------------------------------------------------------------------------
# iwencai API
# ---------------------------------------------------------------------------

def _build_iwencai_referer(question: str) -> str:
    encoded_question = quote(question, safe="")
    return f"{_IWENCAI_ORIGIN}/unifiedwap/result?w={encoded_question}&querytype=stock"


def _build_iwencai_headers(
        hexin_v: str,
        referer: str,
        cookie_header: str,
) -> dict[str, str]:
    host = urlparse(IWENCAI_GET_ROBOT_DATA_URL).netloc or "www.iwencai.com"
    return {
        "User-Agent": _CHROME_USER_AGENT,
        **_browser_common_headers(),
        "Hexin-V": hexin_v,
        "Host": host,
        "Origin": _IWENCAI_ORIGIN,
        "Referer": referer,
        "Cookie": cookie_header,
    }


def _build_iwencai_form_data(
        question: str,
        rsh: str,
        cookies: dict[str, str],
) -> dict[str, str]:
    form_data: dict[str, str] = {
        "source": _IWENCAI_SOURCE,
        "version": "2.0",
        "query_area": "",
        "block_list": "",
        "add_info": _IWENCAI_ADD_INFO,
        "question": question,
        "perpage": "50",
        "page": "1",
        "secondary_intent": "stock",
        "log_info": '{"input_type":"click"}',
        "rsh": rsh,
    }
    userid = cookies.get("userid", "").strip()
    if userid and userid not in {"0", ""}:
        form_data["user_id"] = userid
    return form_data


async def call_ths_wencai(
        question: str,
        *,
        cookie: str | None = None,
) -> dict[str, Any]:
    """
    以 application/x-www-form-urlencoded 调用问财 get-robot-data 接口。
    ``Hexin-V`` 由问财 ``chameleon.js`` 动态生成；
    ``rsh`` 从 cookie ``userid`` / ``other_uid`` 解析（与问财前端逻辑一致）。
    """
    return []
    hexin_v = get_iwencai_hexin_v()
    cookies = _parse_cookie_header(cookie)
    rsh = _resolve_iwencai_rsh(cookies)
    referer = _build_iwencai_referer(question)
    headers = _build_iwencai_headers(hexin_v, referer, _build_iwencai_cookie(hexin_v, cookies))
    form_data = _build_iwencai_form_data(question, rsh, cookies)
    return await _request_json(
        "POST",
        IWENCAI_GET_ROBOT_DATA_URL,
        headers=headers,
        data=form_data,
    )


# ---------------------------------------------------------------------------
# iwencai response parsing
# ---------------------------------------------------------------------------

def _iter_wencai_components(
        wencai_response: dict[str, Any],
) -> Iterator[dict[str, Any]]:
    """遍历问财响应中的表格/组件，便于扩展更多字段解析。"""
    answers = (wencai_response.get("data") or {}).get("answer") or []
    for answer in answers:
        for txt in answer.get("txt") or []:
            content = txt.get("content")
            if not isinstance(content, dict):
                continue
            yield from content.get("components") or []


def _extract_table_column(
        wencai_response: dict[str, Any],
        *,
        table_title: str,
        column_key: str,
) -> list[str]:
    """从指定标题的问财表格组件中提取某一列，去重并保持顺序。"""
    values: list[str] = []
    seen: set[str] = set()
    for component in _iter_wencai_components(wencai_response):
        title_data = (component.get("title_config") or {}).get("data") or {}
        if title_data.get("h1") != table_title:
            continue
        for row in (component.get("data") or {}).get("datas") or []:
            value = row.get(column_key)
            if not value or value in seen:
                continue
            seen.add(value)
            values.append(value)
    return values


def _extract_stock_concepts(wencai_response: dict[str, Any]) -> list[str]:
    """从问财 get-robot-data 响应中提取个股所属概念列表。"""
    return _extract_table_column(
        wencai_response,
        table_title=_WENCAI_CONCEPT_TABLE_TITLE,
        column_key=_WENCAI_CONCEPT_FIELD,
    )


def _ensure_wencai_ok(resp: dict[str, Any]) -> None:
    if resp.get("status_code") == 0:
        return
    raise HTTPException(
        status_code=502,
        detail=resp.get("status_msg") or "问财接口返回异常",
    )


async def wcxg(question: str) -> list[str]:
    """问财查询个股所属概念，返回如 ``['概念1', '概念2', ...]``。"""
    resp = await call_ths_wencai(question)
    _ensure_wencai_ok(resp)
    return _extract_stock_concepts(resp)


# ---------------------------------------------------------------------------
# Hot list helpers
# ---------------------------------------------------------------------------

def _is_a_share_code(code: str) -> bool:
    return code.startswith(_A_SHARE_PREFIXES)


def _format_hot_rate(rate: Any) -> str:
    return str(float(rate)).replace(".0", "")


def _format_hot_rank_change(change: int) -> str:
    if change > 0:
        return f"上升{change}位"
    if change == 0:
        return "无变化"
    return f"下降{change}位"


def _hot_list_tag(stock: dict[str, Any], tag_key: str) -> str:
    tag = stock.get("tag")
    if isinstance(tag, dict) and tag_key in tag:
        return tag[tag_key]
    return "无"


def _build_hot_stock_item(stock: dict[str, Any], *, concept_key: str) -> dict[str, Any]:
    return {
        "市场": "深证" if stock["market"] == 33 else "上证",
        "股票代码": stock["code"],
        "股票名称": stock["name"],
        "热度": _format_hot_rate(stock["rate"]),
        "涨跌": format_percent(stock["rise_and_fall"]),
        "人气排名": stock["order"],
        "人气排名变化": _format_hot_rank_change(stock["hot_rank_chg"]),
        concept_key: _hot_list_tag(stock, "concept_tag"),
        "连板情况": _hot_list_tag(stock, "popularity_tag"),
    }


async def _fetch_hot_list(settings: SettingsDep, list_type: str) -> list[dict[str, Any]]:
    response = await call_ths_api(settings, _HOT_LIST_URL.format(list_type=list_type))
    return response["data"]["stock_list"]


# ---------------------------------------------------------------------------
# Business APIs
# ---------------------------------------------------------------------------

async def stock_fund_flow_individual(symbol, type_):
    """
    同花顺股票资金流
    原接口是获取所有股票的资金流，这里做处理
    """
    individual = ak.stock_fund_flow_individual(type_)
    records = dataframe_to_records(individual)
    if symbol:
        for record in records:
            if str(record["股票代码"]) == str(symbol):
                return record
    return None


async def hot_stock(settings, limit=30):
    """同花顺人气榜。"""
    result = []
    for stock in await _fetch_hot_list(settings, "normal"):
        if _is_a_share_code(stock["code"]):
            result.append(_build_hot_stock_item(stock, concept_key="所属概念"))
    return result[:limit]


async def stock_skyrocket(settings):
    """同花顺人气飙升榜。"""
    result = []
    for stock in await _fetch_hot_list(settings, "skyrocket"):
        try:
            if _is_a_share_code(stock["code"]):
                result.append(_build_hot_stock_item(stock, concept_key="概念"))
        except Exception as exc:
            print(exc)
    return result


async def stock_fund_flow_concept(type_, sort_key, desc=True):
    """同花顺概念资金流。"""
    records = dataframe_to_records(ak.stock_fund_flow_concept(symbol=type_))
    return sort_by_field_and_limit(
        filter_exclude_by_key(records, "行业", ["融资融券", "深股通", "沪股通"]),
        sort_key,
        10,
        desc=desc,
    )


async def zdfb_ths():
    """
    涨跌分布，换成了 call_ths_api_with_header，这个会添加 hexin-v，
    但是这个接口是不是添加的是 v？TODO 还没测试
    """
    return await call_ths_api_with_header(_ZDFB_URL)


async def ggzjl(symbol):
    """个股资金流。"""
    return await call_ths_api_with_header(_STOCK_FUNDS_URL.format(symbol=symbol))


if __name__ == "__main__":
    # 个股资金流
    # ggzjl = asyncio.run(stock_fund_flow_individual(symbol='600519', type_='即时'))
    # print(ggzjl)

    # 概念资金流
    ## 即时：按涨跌幅排名
    # gnzjl = asyncio.run(stock_fund_flow_concept('即时', '行业-涨跌幅'))
    ## 3日，按资金流入排名
    # gnzjl = asyncio.run(stock_fund_flow_concept('3日排行', '流入资金'))
    # print(json.dumps(gnzjl, ensure_ascii=False, indent=2))

    concepts = asyncio.run(wcxg("002580"))
    print(json.dumps(concepts, ensure_ascii=False, indent=2))
