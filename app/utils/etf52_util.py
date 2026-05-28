import asyncio
import json

from app.utils.common_util import list_to_dict_v2, round_half_up
from app.utils.http_util import get_api


def _fmt_turnover_yi(amount: float) -> str:
    return str(round_half_up(amount / 100000000, 2)) + "亿"


def _build_turnover_block(trading: dict, *, market_phase: str) -> dict:
    """52etf trading 字段：turnover=今日累计，turnover_pre=昨日全天，turnover_change=较昨日同时段。"""
    delta = round_half_up(trading["turnover_change"] / 100000000, 2)
    block: dict = {
        "今日累计": _fmt_turnover_yi(trading["turnover"]),
        "昨日全天": _fmt_turnover_yi(trading["turnover_pre"]),
        "较昨日同时段": ("放量" if delta > 0 else "缩量") + str(abs(delta)) + "亿",
    }
    predict = trading.get("predict_turnover")
    if predict is not None:
        block["预测全天"] = _fmt_turnover_yi(predict)
    if market_phase == "closed":
        block["今日全天"] = block["今日累计"]
        block["口径说明"] = (
            "收盘后：今日累计≈今日全天，可与昨日全天对比；较昨日同时段为数据源同时刻差值。"
        )
    else:
        block["口径说明"] = (
            "盘中/盘前/午间：今日累计仅截至采集时刻，不可与昨日全天直接比较；"
            "判断放量/缩量以「较昨日同时段」为准；预测全天为数据源估算。"
        )
    return block


async def zdfb_52etf(*, market_phase: str = "intraday"):
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
        "成交额": _build_turnover_block(trading, market_phase=market_phase),
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
