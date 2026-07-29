"""个股主力资金净额解析（统一口径）。

不同模块（评分维度 / 盘中买入门禁 / 推送展示 / 资金快照）都需要"当日主力净流入"，
口径在此集中，避免各处各取不同字段：

- 盘中（during_market）：始终用 ``个股资金流`` 的 ``大单流入 − 大单流出``。
- 晚间复盘（及其他模式）：优先取 ``个股资金流日线`` 最新一条；若其日期为当天，
  用其 ``主力净流入-净额``（= 超大单+大单）；否则回退 ``大单流入 − 大单流出``。

原始值常带「万 / 亿」单位且可正可负，``amount_to_yuan`` 统一换算为元。
"""

from __future__ import annotations

import re
from typing import Any

from common.timeutil import cn_today


def amount_to_yuan(v: object) -> float | None:
    """带单位金额 → 元：识别「亿」「万」，无单位视为元；数值可正可负。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    val = float(m.group())
    if "亿" in s:
        val *= 1e8
    elif "万" in s:
        val *= 1e4
    return val


def intraday_big_net_yuan(flow: dict | None) -> float | None:
    """盘中「个股资金流」大单流入 − 大单流出，单位元。"""
    if not isinstance(flow, dict):
        return None
    bi = amount_to_yuan(flow.get("大单流入"))
    bo = amount_to_yuan(flow.get("大单流出"))
    if bi is None or bo is None:
        return None
    return bi - bo


def daily_latest_today_net_yuan(daily: list[dict] | None, today_s: str) -> float | None:
    """「个股资金流日线」最新一条若为当天，返回其主力净流入-净额（元）；否则 None。"""
    if not isinstance(daily, list) or not daily:
        return None
    latest = daily[-1]
    if not isinstance(latest, dict):
        return None
    d = str(latest.get("日期") or "").strip()
    if not today_s or not d.startswith(today_s):
        return None
    return amount_to_yuan(latest.get("主力净流入-净额"))


def daily_net_yuan(row: dict) -> float | None:
    """日线一行的主力净额（元），用于连续流出统计。"""
    if not isinstance(row, dict):
        return None
    for key in ("主力净流入-净额", "净额", "净流入"):
        v = row.get(key)
        if v is None:
            continue
        yuan = amount_to_yuan(v)
        if yuan is not None:
            return yuan
    return None


def resolve_main_net_yuan(
    stock: dict,
    *,
    mode: str,
    today_s: str = "",
) -> tuple[float | None, str]:
    """按模式解析当日主力净流入（元）及其来源。

    返回 (净额元, 来源标签)。来源：``日线-当日`` / ``大单流入-大单流出`` / ``无数据``。
    """
    if not isinstance(stock, dict):
        return None, "无数据"
    flow = stock.get("个股资金流")
    flow = flow if isinstance(flow, dict) else {}
    daily = stock.get("个股资金流日线")
    daily = daily if isinstance(daily, list) else []

    big_net = intraday_big_net_yuan(flow)

    if mode == "during_market":
        # 智能盯盘：始终用大单流入 − 大单流出
        return big_net, "大单流入-大单流出"

    # 晚间复盘及其他：日线当日优先，否则回退大单净
    today = today_s or cn_today().isoformat()
    daily_net = daily_latest_today_net_yuan(daily, today)
    if daily_net is not None:
        return daily_net, "日线-当日"
    if big_net is None:
        return None, "无数据"
    return big_net, "大单流入-大单流出"


def intraday_main_net_yuan(stock: dict) -> float | None:
    """个股盘中主力净额（元）= 大单流入 − 大单流出。

    买入门禁 / 推送展示 / 资金快照统一用此口径（与 stock_fund_flow 维度一致）。
    """
    if not isinstance(stock, dict):
        return None
    return intraday_big_net_yuan(stock.get("个股资金流"))


def intraday_main_net_wan(stock: dict) -> float | None:
    """个股盘中主力净额（万元）。"""
    yuan = intraday_main_net_yuan(stock)
    return None if yuan is None else yuan / 10000.0
