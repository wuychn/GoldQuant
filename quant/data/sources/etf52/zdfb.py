"""52etf 涨跌分布数据源。"""

from __future__ import annotations

import asyncio
import json

from app.utils.common_util import list_to_dict_v2, round_half_up
from app.utils.http_util import get_api


def _fmt_turnover_yi(amount: float) -> str:
    return str(round_half_up(amount / 100000000, 2)) + "亿"


def _build_turnover_block(trading: dict, *, market_phase: str) -> dict:
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
    return block


async def zdfb_52etf(*, market_phase: str = "intraday"):
    url = "https://52etf.site/api/market/topstock"
    r = await get_api(url)
    up_down = r["thsData"]["upDownData"]
    trading = r["thsData"]["trading"]
    zdfb_ = list_to_dict_v2(up_down["table"], "key", "value")
    return {
        "下跌": up_down["down"],
        "上涨": up_down["up"],
        "平盘": up_down["flat"],
        "涨停": up_down["limit_up"],
        "跌停": up_down["limit_down"],
        "成交额": _build_turnover_block(trading, market_phase=market_phase),
        "涨跌分布": {
            ">10%": zdfb_[">10%"],
            "7%~10%": zdfb_["7~10"],
            "5%~7%": zdfb_["5~7"],
            "3%~5%": zdfb_["3~5"],
            "0%~3%": zdfb_["0~3"],
            "0%": zdfb_["0"],
            "-3%~0%": zdfb_["3~0"],
            "-5%~-3%": zdfb_["5~3"],
            "-7%~-5%": zdfb_["7~5"],
            "-10%~-7%": zdfb_["10~7"],
        },
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(zdfb_52etf()), ensure_ascii=False, indent=2))
