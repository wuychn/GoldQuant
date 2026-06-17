import json

import akshare as ak
import requests

from app.utils.common_util import sort_by_field_and_limit, today, get_val, set_field_value, list_to_dict, \
    get_n_workdays_ago
from app.utils.dataframe import dataframe_to_records

_JBXX_EM_URL = "https://push2.eastmoney.com/api/qt/stock/get"
_JBXX_EM_FIELDS = (
    "f120,f121,f122,f174,f175,f59,f163,f43,f57,f58,f169,f170,f46,f44,f51,f168,f47,"
    "f164,f116,f60,f45,f52,f50,f48,f167,f117,f71,f161,f49,f530,f135,f136,f137,f138,"
    "f139,f141,f142,f144,f145,f147,f148,f140,f143,f146,f149,f55,f62,f162,f92,f173,f104,"
    "f105,f84,f85,f183,f184,f185,f186,f187,f188,f189,f190,f191,f192,f107,f111,f86,f177,f78,"
    "f110,f262,f263,f264,f267,f268,f255,f256,f257,f258,f127,f199,f128,f198,f259,f260,f261,"
    "f171,f277,f278,f279,f288,f152,f250,f251,f252,f253,f254,f269,f270,f271,f272,f273,f274,"
    "f275,f276,f265,f266,f289,f290,f286,f285,f292,f293,f294,f295,f43"
)
_JBXX_FIELD_MAP = {
    "f57": "股票代码",
    "f58": "股票简称",
    "f84": "总股本",
    "f85": "流通股",
    "f127": "行业",
    "f116": "总市值",
    "f117": "流通市值",
    "f189": "上市时间",
    "f43": "最新",
}


def _parse_stock_individual_info_payload(data_json: dict) -> dict:
    """从东财 ``push2`` JSON 提取 jbxx 字段；忽略 ``dsc``/``dlmkts`` 等顶层噪声。"""
    if not isinstance(data_json, dict):
        return {}
    data = data_json.get("data")
    if not isinstance(data, dict):
        return {}
    return {name: data[fk] for fk, name in _JBXX_FIELD_MAP.items() if fk in data}


def _fetch_stock_individual_info_em(symbol: str, *, timeout: float = 15) -> dict:
    sym = str(symbol).strip()
    if not sym:
        return {}
    market_code = 1 if sym.startswith("6") else 0
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": _JBXX_EM_FIELDS,
        "secid": f"{market_code}.{sym}",
    }
    r = requests.get(_JBXX_EM_URL, params=params, timeout=timeout)
    r.raise_for_status()
    return _parse_stock_individual_info_payload(r.json())


def jbxx(symbol):
    """
    基本信息（东财 push2 API，直接解析 ``data``，规避 akshare DataFrame 列数 bug）。
    """
    return _fetch_stock_individual_info_em(str(symbol))


def pk(symbol):
    """
    盘口
    :param symbol:
    :return:
    """
    records = dataframe_to_records(ak.stock_bid_ask_em(symbol=str(symbol)))
    result = []
    for record in records:
        item_ = record['item']
        item_ = item_.replace('buy_', '买').replace('sell_', '卖').replace('_vol', '量（单位：手）')
        record['item'] = item_
        result.append(record)
    return list_to_dict(result)


def ztgc_with_date(trade_date):
    records = dataframe_to_records(ak.stock_zt_pool_previous_em(date=trade_date))
    for item in records:
        val = get_val(item, "涨停统计", '')
        set_field_value(item, "涨停统计", val.replace("/", "天") + "板")
    return sort_by_field_and_limit(records, "连板数", limit=1000)


def ztgc(filter_first: bool = False):
    """
    当日涨停股池（东财 ``stock_zt_pool_em``），如果 filter_first 为 True 则过滤首板 TODO 需要注意是不是实时的
    """
    records = dataframe_to_records(ak.stock_zt_pool_em(date=today()))

    filtered_records = [
        item for item in records
        if not filter_first or get_val(item, "连板数", 0) > 1
    ]

    for item in filtered_records:
        val = get_val(item, "涨停统计", '')
        set_field_value(item, "涨停统计", val.replace("/", "天") + "板")

    return sort_by_field_and_limit(filtered_records, "连板数", limit=1000)


def zj(symbol):
    """
    个股资金流向（日线级）；返回较近一段记录，供调用方按交易日再截断。
    """
    recs = dataframe_to_records(
        ak.stock_individual_fund_flow(
            stock=str(symbol), market="sh" if str(symbol).startswith("6") else "sz"
        )
    )
    if not recs:
        return []
    return recs[-120:] if len(recs) > 120 else recs

def pkyd(symbol):
    """
    盘口异动
    :return:
    """
    pd = ak.stock_changes_em(symbol)
    return dataframe_to_records(pd)


def hsgtzj():
    """
    沪深港通资金流向
    """
    return dataframe_to_records(ak.stock_hsgt_fund_flow_summary_em())


def cmfb(symbol):
    """
    筹码分布，取最新5条
    """
    return dataframe_to_records(ak.stock_cyq_em(symbol=str(symbol), adjust=""))[-5:]


def hist(symbol, period='daily', *, start_date=None, end_date=None):
    """
    个股历史行情。

    ``start_date`` / ``end_date`` 为 ``YYYYMMDD``（或不带前导零的东财口径）；不传时按周期使用默认回溯窗口。
    日线全量/增量由调用方传入 ``start_date`` 控制。
    """
    end = end_date or today()
    if start_date is not None:
        start = start_date
    else:
        n = 9
        if period == 'daily':
            n = 9
        elif period == 'weekly':
            n = 27
        elif period == 'monthly':
            n = 60
        start = get_n_workdays_ago(n=n)
    return dataframe_to_records(
        ak.stock_zh_a_hist(symbol=str(symbol), period=period, start_date=start, end_date=end))


if __name__ == "__main__":
    print(json.dumps(jbxx("600519"), ensure_ascii=False, indent=2))
    # print(hqbj_dc(600519))
    # 60日大幅上涨
    # print(json.dumps(pkyd('60日大幅上涨'), ensure_ascii=False, indent=2))
    # print(hqbj("002580"))
    # print(json.dumps(ztgc(), ensure_ascii=False, indent=2))
