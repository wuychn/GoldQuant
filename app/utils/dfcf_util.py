import json

import akshare as ak

from app.utils.common_util import sort_by_field_desc_and_limit, today, get_val, set_field_value, list_to_dict, \
    get_n_workdays_ago
from app.utils.dataframe import dataframe_to_records


def jbxx(symbol):
    """
    基本信息
    :return:
    """
    return list_to_dict(dataframe_to_records(ak.stock_individual_info_em(symbol=str(symbol))))


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


def lshq(symbol):
    """
    历史行情
    :param symbol:
    :return:
    """
    # 使用东方财富
    return dataframe_to_records(
        ak.stock_zh_a_hist(symbol=str(symbol), start_date=get_n_workdays_ago(), end_date=today()))
    # 可以使用使用新浪
    # ak.stock_zh_a_daily()


def lhbrq(symbol):
    """
    龙虎榜日期
    :return:
    """
    return dataframe_to_records(ak.stock_lhb_stock_detail_date_em(symbol=str(symbol)))


def lhbxq(symbol, date, type_):
    """
    龙虎榜详情
    :param symbol:
    :param date:
    :param type_:
    :return:
    """
    return dataframe_to_records(ak.stock_lhb_stock_detail_em(symbol=str(symbol), date=date, flag=type_))


def xw(symbol):
    """
    个股新闻
    :param symbol:
    :return:
    """
    return dataframe_to_records(ak.stock_news_em(symbol=str(symbol)))


def ztgc():
    """
    涨停股
    """
    records = dataframe_to_records(ak.stock_zt_pool_em(date=today()))

    # 过滤：字段值大于1
    filtered_records = [
        item for item in records
        if get_val(item, "连板数", 0) > 1
    ]

    for item in filtered_records:
        val = get_val(item, "涨停统计", '')
        set_field_value(item, "涨停统计", val.replace("/", "天") + "板")

    return sort_by_field_desc_and_limit(filtered_records, "连板数", limit=1000)


def zj(symbol):
    """
    个股资金，取最新5条
    """
    return dataframe_to_records(
        ak.stock_individual_fund_flow(stock=str(symbol), market="sh" if str(symbol).startswith("6") else "sz"))[-5:]


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


def hist(symbol):
    """
    个股历史行情
    """
    return dataframe_to_records(
        ak.stock_zh_a_hist(symbol=str(symbol), start_date=get_n_workdays_ago(n=9), end_date=today()))


def ggjbxx(symbol):
    """
    获取个股基本信息
    :param symbol:
    :return:
    """
    jbxx_ = jbxx(symbol)

    # 个股买卖报价（买卖盘口）
    pk_ = pk(symbol)

    # 个股历史行情
    lshq_ = lshq(symbol)

    # 个股龙虎榜日期
    lhbrq_ = lhbrq(symbol)

    # 个股龙虎榜详情
    lhbmr = lhbxq(symbol, today(), '买入')
    lhbmc = lhbxq(symbol, today(), '卖出')

    # 个股新闻
    xw_ = xw(symbol)


if __name__ == "__main__":
    print(json.dumps(jbxx(600519), ensure_ascii=False, indent=2))
    # print(hqbj_dc(600519))
    # print(hqbj("002580"))
    # print(json.dumps(ztgc(), ensure_ascii=False, indent=2))
