import asyncio
import json
from typing import Any

import akshare as ak
import httpx
import py_mini_racer
from akshare.stock_feature.stock_fund_flow import _get_file_content_ths
from fastapi import HTTPException

from app.api.deps import SettingsDep
from app.utils.common_util import format_percent, sort_by_field_desc_and_limit, filter_exclude_by_key
from app.utils.dataframe import dataframe_to_records


def get_v():
    js_code = py_mini_racer.MiniRacer()
    js_content = _get_file_content_ths("ths.js")
    js_code.eval(js_content)
    return js_code.call("v")


async def call_ths_api(
        settings: SettingsDep,
        url: str
):
    """
    直接调用同花顺接口
    """
    headers = {
        "User-Agent": settings.THS_DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }
    # 这里是添加同花顺的请求头，现在同花顺接口的Cookie还不知道怎么绕过 TODO
    # headers = merge_ths_headers_for_url(url, headers)
    client_kw: dict[str, Any] = {"timeout": settings.HTTP_CLIENT_TIMEOUT}
    if px := settings.httpx_proxy_url():
        client_kw["proxy"] = px
    try:
        async with httpx.AsyncClient(**client_kw) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def call_ths_api_with_header(
        url: str
):
    """
    直接调用同花顺接口
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7,ko;q=0.6",
        "Connection": "keep-alive",
        "Cookie": "keep-alive",
        "Hexin-V": get_v(),
        "Host": "stockpage.10jqka.com.cn",
        "Referer": "https://stockpage.10jqka.com.cn/002580/funds/",
        "Sec-Ch-Ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "x-requested-with": "XMLHttpRequest"
    }
    client_kw: dict[str, Any] = {"timeout": 30}
    try:
        async with httpx.AsyncClient(**client_kw) as client:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
    """
    同花顺人气榜
    """
    r = await call_ths_api(
        settings,
        "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal"
    )
    result = []
    for s in r['data']['stock_list']:
        code_ = s['code']
        if code_.startswith('60') or code_.startswith('0') or code_.startswith('3'):
            result.append({
                '市场': '深证' if s['market'] == 33 else '上证',
                '股票代码': s['code'],
                '股票名称': s['name'],
                '热度': str(float(s['rate'])).replace(".0", ''),
                '涨跌': format_percent(s['rise_and_fall']),
                '人气排名': s['order'],
                '人气排名变化': f'上升{s["hot_rank_chg"]}位' if s['hot_rank_chg'] > 0 else '无变化' if s[
                                                                                                           'hot_rank_chg'] == 0 else f'下降{s["hot_rank_chg"]}位',
                '所属概念': s['tag']['concept_tag'] if 'tag' in s and 'concept_tag' in s['tag'] else '无',
                '连板情况': s['tag']['popularity_tag'] if 'tag' in s and 'popularity_tag' in s['tag'] else '无'
            })
    return result[:limit]


async def stock_skyrocket(settings):
    """
    同花顺人气飙升榜
    """
    r = await call_ths_api(
        settings,
        "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=skyrocket"
    )
    result = []
    for s in r['data']['stock_list']:
        try:
            code_ = s['code']
            if code_.startswith('60') or code_.startswith('0') or code_.startswith('3'):
                result.append({
                    '市场': '深证' if s['market'] == 33 else '上证',
                    '股票代码': s['code'],
                    '股票名称': s['name'],
                    '热度': str(float(s['rate'])).replace(".0", ''),
                    '涨跌': format_percent(s['rise_and_fall']),
                    '人气排名': s['order'],
                    '人气排名变化': f'上升{s["hot_rank_chg"]}位' if s['hot_rank_chg'] > 0 else '无变化' if s[
                                                                                                               'hot_rank_chg'] == 0 else f'下降{s["hot_rank_chg"]}位',
                    '概念': s['tag']['concept_tag'] if 'tag' in s and 'concept_tag' in s['tag'] else '无',
                    '连板情况': s['tag']['popularity_tag'] if 'tag' in s and 'popularity_tag' in s['tag'] else '无'
                })
        except Exception as e:
            print(e)
            pass
    return result


async def stock_fund_flow_concept(type_, sort_key):
    """
    同花顺概念资金流
    """
    records = dataframe_to_records(ak.stock_fund_flow_concept(symbol=type_))
    return sort_by_field_desc_and_limit(filter_exclude_by_key(records, '行业', ['融资融券', '深股通', '沪股通']),
                                        sort_key, 10)


async def zdfb_ths():
    """
    涨跌分布，换成了call_ths_api_with_header，这个会添加hexin-v，但是这个接口是不是添加的是v？TODO 还没测试
    """
    r = await call_ths_api_with_header(
        "https://q.10jqka.com.cn/api.php?t=indexflash&"
    )
    return r


async def ggzjl(symbol):
    """
    个股资金流
    :return:
    """
    return await call_ths_api_with_header(f'https://stockpage.10jqka.com.cn/spService/{symbol}/Funds/realFunds/free/1/')


if __name__ == "__main__":
    # 个股资金流
    # ggzjl = asyncio.run(stock_fund_flow_individual(symbol='600519', type_='即时'))
    # print(ggzjl)

    # 概念资金流
    ## 即时：按涨跌幅排名
    # gnzjl = asyncio.run(stock_fund_flow_concept('即时', '行业-涨跌幅'))
    ## 3日，按资金流入排名
    gnzjl = asyncio.run(stock_fund_flow_concept('3日排行', '流入资金'))
    print(json.dumps(gnzjl, ensure_ascii=False, indent=2))
