import asyncio
import json

from app.utils.common_util import list_to_dict_v2, round_half_up
from app.utils.http_util import get_api


async def zdfb_52etf():
    """
    这个接口可以获取很多数据，包括上证、深证、创业板等，以及涨跌数据、成交数据
    https://52etf.site/api/market/topstock
    """
    url = 'https://52etf.site/api/market/topstock'
    r = await get_api(url)
    # 涨跌家数等数据
    upDownData = r['thsData']['upDownData']
    trading = r['thsData']['trading']
    zdfb_ = list_to_dict_v2(upDownData['table'], 'key', 'value')
    result = {
        "下跌": upDownData['down'],
        "上涨": upDownData['up'],
        "平盘": upDownData['flat'],
        "涨停": upDownData['limit_up'],
        "跌停": upDownData['limit_down'],
        "今日成交额": str(round_half_up(trading['turnover'] / 100000000, 2)) + '亿',
        "昨日成交额": str(round_half_up(trading['turnover_pre'] / 100000000, 2)) + '亿',
        "较昨日变动": ('放量' if round_half_up(trading['turnover_change'] / 100000000, 2) > 0 else '缩量') + str(
            round_half_up(trading['turnover_change'] / 100000000, 2)) + '亿',
        "涨跌分布": {
            '>10%': zdfb_['>10%'],
            '7%~10%': zdfb_['7~10'],
            '5%~7%': zdfb_['5~7'],
            '3%~5%': zdfb_['3~5'],
            '0%~3%': zdfb_['0~3'],
            '0%': zdfb_['0'],
            '-3%~0%': zdfb_['3~0'],
            '-5%~-3%': zdfb_['5~3'],
            '-7%~-5%': zdfb_['7~5'],
            '-10%~-7%': zdfb_['10~7'],
        }
    }
    return result


if __name__ == "__main__":
    r = asyncio.run(zdfb_52etf())
    print(json.dumps(r, ensure_ascii=False, indent=2))
