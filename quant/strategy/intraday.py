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
from quant.store.intraday_fund_track import net_flow_improving

_HMS_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def is_continuous_auction_minute(ts: object) -> bool:
    """A 股连续竞价时段：09:30–11:30、13:00–15:00（含端点）。"""
    m = _HMS_RE.search(str(ts or ""))
    if not m:
        return False
    h, mi = int(m.group(1)), int(m.group(2))
    tot = h * 60 + mi
    return (9 * 60 + 30 <= tot <= 11 * 60 + 30) or (13 * 60 <= tot <= 15 * 60)


def session_minute_bars(stock: dict) -> list[dict]:
    """连续竞价分钟 K（09:30 起，含成交量）。"""
    raw = stock.get("分钟行情") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        ts = str(row.get("时间") or "")
        if not is_continuous_auction_minute(ts):
            continue
        close = to_float(row.get("收盘") or row.get("最新价"))
        if close is None or close <= 0:
            continue
        vol = to_float(row.get("成交量")) or 0.0
        opn = to_float(row.get("开盘")) or close
        out.append({"open": opn, "close": close, "vol": vol, "time": ts})
    return out


def _session_minute_bars(stock: dict) -> list[dict]:
    return session_minute_bars(stock)


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


def _above_vwap_ratio(bars: list[dict], vwap: float, lookback: int) -> float:
    recent = bars[-lookback:] if bars else []
    if not recent or vwap <= 0:
        return 0.0
    above = sum(1 for b in recent if b["close"] >= vwap)
    return above / len(recent)


def _minute_momentum_improving(recent: list[dict], relax: float) -> bool:
    if len(recent) < 6:
        return False
    flows = [(b["close"] - b["open"]) * b["vol"] for b in recent]
    mid = len(flows) // 2
    early_sum = sum(flows[:mid])
    late_sum = sum(flows[mid:])
    if early_sum > 0:
        need = early_sum * relax
    elif early_sum < 0:
        need = early_sum / relax
    else:
        need = 0.0
    return late_sum > need


def _intraday_cfg(buy_cfg: dict[str, Any] | None) -> dict[str, Any]:
    c = buy_cfg or {}
    intra = dict(c.get("intraday") or {})
    intra.setdefault("enabled", True)
    return intra


def intraday_allows_buy(
    stock: dict,
    buy_cfg: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """盘中买入前置：均价上方占比 + 近端走势 + 资金改善（可负但收敛）。"""
    intra = _intraday_cfg(buy_cfg)
    if not intra.get("enabled", True):
        return True, ""

    last = quote_last_price(stock)
    if not last or last <= 0:
        return False, "无有效现价"

    code = str(stock.get("股票代码", "")).strip()
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    bars = _session_minute_bars(stock)
    avg = quote_avg_price(stock) or _vwap(bars)
    vwap = avg or _vwap(bars)

    # --- 1. 现价须在分时均价上方 ---
    if intra.get("require_above_avg", True):
        margin = float(intra.get("avg_margin_pct", 0.0))
        floor = vwap * (1 + margin / 100) if vwap else None
        if floor is None or last < floor:
            avg_s = f"{vwap:.2f}" if vwap else "—"
            return False, f"现价{last:.2f}低于分时均价{avg_s}"

    lookback = max(3, int(intra.get("lookback_minutes", 15)))
    recent = bars[-lookback:] if bars else []

    # --- 2. 近 N 分钟多数收在 VWAP 上方 ---
    min_above_ratio = float(intra.get("min_above_vwap_ratio", 0.65))
    if vwap and len(recent) >= 5:
        ratio = _above_vwap_ratio(bars, vwap, lookback)
        if ratio < min_above_ratio:
            return False, f"分时仅{ratio:.0%}在均价上方(需≥{min_above_ratio:.0%})"

    # --- 3. 近 N 分钟走势不能持续下行 ---
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

    # --- 4. 资金/动能：强势豁免 OR 净流入改善 OR 分钟动能改善 OR 当前净流入 ---
    if intra.get("require_strength_signal", True):
        chg = quote_change_pct(stock)
        skip_min_chg = float(intra.get("skip_strength_min_day_chg", 5.0))
        strong_day = (
            chg is not None
            and chg >= skip_min_chg
            and vwap
            and last >= vwap
        )

        flow = stock.get("个股资金流") or {}
        net = _parse_amount_wan(flow.get("净额")) if isinstance(flow, dict) else None
        min_delta = float(intra.get("fund_improve_min_delta_wan", 50.0))
        net_ok = net is not None and net > 0
        improving = net_flow_improving(code, min_delta_wan=min_delta, current_net=net)
        relax = float(intra.get("minute_momentum_relax_ratio", 0.85))
        minute_ok = _minute_momentum_improving(recent, relax)

        if not (strong_day or net_ok or improving or minute_ok):
            if net is not None and net < 0 and not improving:
                return False, "净流出且未见改善"
            return False, "分时强势证据不足"

    # --- 5. 日内走弱：距高点回撤 / 当日涨幅 ---
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

    flow = stock.get("个股资金流") or {}
    if isinstance(flow, dict) and flow:
        net = _parse_amount_wan(flow.get("净额"))
        hard = float(intra.get("hard_net_outflow_wan", 8000))
        if net is not None and net < -hard:
            return False, f"当日净流出{abs(net):.0f}万过大"

    return True, "分时强势"


def _intraday_weakness_cfg(sell_cfg: dict[str, Any] | None) -> dict[str, Any]:
    c = sell_cfg or {}
    weak = dict(c.get("intraday_weakness") or {})
    weak.setdefault("enabled", True)
    return weak


def _session_high(stock: dict, bars: list[dict]) -> float | None:
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    high = to_float(pk.get("最高"))
    if high and high > 0:
        return high
    if not bars:
        return None
    return max(b["close"] for b in bars)


def intraday_weakness_triggers_sell(
    stock: dict,
    sell_cfg: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """盘中走弱卖出：冲高回落 + 有效跌破分时均价 + 近端分时确认。

    与买入 ``intraday_allows_buy`` 对称，用于持仓止盈/避险，不依赖破 5 日线。
    """
    cfg = _intraday_weakness_cfg(sell_cfg)
    if not cfg.get("enabled", True):
        return False, ""

    bars = _session_minute_bars(stock)
    min_bars = max(3, int(cfg.get("min_minute_bars", 8)))
    if len(bars) < min_bars:
        return False, ""

    last = quote_last_price(stock)
    if not last or last <= 0:
        return False, ""

    high = _session_high(stock, bars)
    if not high or high <= 0:
        return False, ""

    dd_pct = (last - high) / high * 100
    min_dd = float(cfg.get("max_drop_from_high_pct", 3.0))
    if dd_pct > -min_dd:
        return False, ""

    avg = quote_avg_price(stock) or _vwap(bars)
    if not avg or avg <= 0:
        return False, ""

    margin = float(cfg.get("avg_break_margin_pct", 0.15))
    ceiling = avg * (1 - margin / 100)
    if last >= ceiling:
        return False, ""

    if cfg.get("require_recent_weakness", True):
        lookback = max(3, int(cfg.get("lookback_minutes", 10)))
        recent = bars[-lookback:]
        if len(recent) >= 3:
            closes = [b["close"] for b in recent]
            drop_pct = (closes[-1] - closes[0]) / closes[0] * 100
            min_recent_drop = float(cfg.get("min_recent_drop_pct", 0.25))
            if drop_pct > -min_recent_drop:
                return False, ""

            down_steps = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
            min_down_ratio = float(cfg.get("min_down_bars_ratio", 0.55))
            if down_steps / (len(closes) - 1) < min_down_ratio:
                return False, ""

    return (
        True,
        f"距日内高点回撤{abs(dd_pct):.2f}%，现价{last:.2f}有效低于分时均价{avg:.2f}",
    )
