import akshare as ak

def ggxxcx_dc(symbol):
    """
    个股信息查询（东财）
    :return:
    """
    return ak.stock_individual_info_em(symbol=str(symbol))

def bsb_dc():
    """
    飙升榜（东财）
    :return:
    """
    return ak.stock_hot_up_em()

def hqbj_dc(symbol):
    """
    行情报价（买卖队列、涨跌幅等）
    :param symbol:
    :return:
    """
    stock_bid_ask_em_df = ak.stock_bid_ask_em(symbol=str(symbol))
    print(stock_bid_ask_em_df)

if __name__ == "__main__":
    # print(ggxxcx_dc(600519))
    # print(hqbj_dc(600519))
    print(bsb_dc())