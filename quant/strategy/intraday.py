"""盘中买入：分时走势 / 均价线 / 资金改善过滤。"""

from __future__ import annotations

import re
from typing import Any

from quant.scoring.tech_indicators import (
    quote_avg_price,
    quote_change_pct,
    quote_last_price,
    to_float,
)


def _parse_amount_wan(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    if not m:
        return None
    return float(m.group())


def _session_minute_bars(stock: dict) -> list[dict]:
    """连续竞价分钟 K（09:30 起，含成交量）。"""
    raw = stock.get("分钟行情") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        ts = str(row.get("时间") or "")
        # 09:30 及之后；排除纯集合竞价 09:15–09:25
        if not any(x in ts for x in ("09:3", "09:4", "09:5", "10:", "11:", "13:", "14:", "15:0")):
            continue
        close = to_float(row.get("收盘") or row.get("最新价"))
        if close is None or close <= 0:
            continue
        vol = to_float(row.get("成交量")) or 0.0
        opn = to_float(row.get("开盘")) or close
        out.append({"open": opn, "close": close, "vol": vol, "time": ts})
    return out


def _vwap(bars: list[dict]) -> float | None:
    num = 0.0
    den = 0.0
    for b in bars:
        v = b["vol"]
        if v <= 0:
            continue
        num += b["close"] * v
        den += v
    return num / den if den > 0 else None


def _intraday_cfg(buy_cfg: dict[str, Any] | None) -> dict[str, Any]:
    c = buy_cfg or {}
    intra = dict(c.get("intraday") or {})
    intra.setdefault("enabled", True)
    return intra


def intraday_allows_buy(
    stock: dict,
    buy_cfg: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """盘中买入前置：非下行、均价线上方、分时资金改善、走势不过弱。"""
    intra = _intraday_cfg(buy_cfg)
    if not intra.get("enabled", True):
        return True, ""

    last = quote_last_price(stock)
    if not last or last <= 0:
        return False, "无有效现价"

    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    bars = _session_minute_bars(stock)

    # --- 1. 现价须在分时均价上方 ---
    if intra.get("require_above_avg", True):
        avg = quote_avg_price(stock) or _vwap(bars)
        margin = float(intra.get("avg_margin_pct", 0.0))
        floor = avg * (1 + margin / 100) if avg else None
        if floor is None or last < floor:
            avg_s = f"{avg:.2f}" if avg else "—"
            return False, f"现价{last:.2f}低于分时均价{avg_s}"

    # --- 2. 近 N 分钟走势不能持续下行 ---
    lookback = max(3, int(intra.get("lookback_minutes", 15)))
    recent = bars[-lookback:] if bars else []
    if len(recent) >= 3:
        closes = [b["close"] for b in recent]
        drop_pct = (closes[-1] - closes[0]) / closes[0] * 100
        max_drop = float(intra.get("max_recent_drop_pct", 0.35))
        if drop_pct < -max_drop:
            return False, f"近{len(recent)}分钟走势下行({drop_pct:.2f}%)"

        down_steps = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
        max_down_ratio = float(intra.get("max_down_bars_ratio", 0.55))
        if down_steps / (len(closes) - 1) > max_down_ratio:
            return False, f"近{len(recent)}分钟跌多涨少"

        # --- 3. 分时资金呈改善（后半段强于前半段；净额可为负） ---
        if intra.get("require_fund_flow_improve", True):
            flows = [(b["close"] - b["open"]) * b["vol"] for b in recent]
            if len(flows) >= 6:
                mid = len(flows) // 2
                early_sum = sum(flows[:mid])
                late_sum = sum(flows[mid:])
                if late_sum <= early_sum:
                    return False, "分时资金未见改善"

    # --- 4. 日内走弱：距高点回撤 / 当日涨幅 ---
    high = to_float(pk.get("最高"))
    if high and high > 0:
        dd = (last - high) / high * 100
        max_dd = float(intra.get("max_drop_from_high_pct", 2.5))
        if dd < -max_dd:
            return False, f"距日内高点回撤{abs(dd):.2f}%过大"

    chg = quote_change_pct(stock)
    min_chg = float(intra.get("min_day_change_pct", -1.5))
    if chg is not None and chg < min_chg:
        return False, f"当日涨幅{chg:.2f}%偏弱"

    # 快照资金：极端净流出且分钟也未改善时拦截
    flow = stock.get("个股资金流") or {}
    if isinstance(flow, dict) and flow:
        net = _parse_amount_wan(flow.get("净额"))
        hard = float(intra.get("hard_net_outflow_wan", 8000))
        if net is not None and net < -hard:
            return False, f"当日净流出{abs(net):.0f}万过大"

    return True, "分时强势"
