import akshare as ak

from app.utils.common_util import sort_by_field_desc_and_limit, today, get_val, set_field_value
from app.utils.dataframe import dataframe_to_records


def ggxxcx(symbol):
    """
    个股信息查询（东财）
    :return:
    """
    return ak.stock_individual_info_em(symbol=str(symbol))


def bsb():
    """
    飙升榜（东财）
    :return:
    """
    return ak.stock_hot_up_em()


def hqbj(symbol):
    """
    行情报价（买卖队列、涨跌幅等）
    :param symbol:
    :return:
    """
    return ak.stock_bid_ask_em(symbol=str(symbol))


def zfqsgn():
    records = dataframe_to_records(ak.stock_board_concept_name_em())
    return sort_by_field_desc_and_limit(records, "涨跌幅")


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


if __name__ == "__main__":
    print(ggxxcx(600519))
    # print(hqbj_dc(600519))
    # print(hqbj("002580"))
    # print(json.dumps(ztgc(), ensure_ascii=False, indent=2))
