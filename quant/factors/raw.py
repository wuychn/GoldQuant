"""原始因子：主升浪结构 / 资金流 / 历史动量。"""

from __future__ import annotations

from typing import Any

from quant.factors.base import FactorSpec
from quant.market.fund_flow import resolve_main_net_yuan
from quant.scoring.tech_indicators import (
    hist_closes,
    hist_daily_changes,
    ma_spread_pct,
)
from quant.factors.universe import float_market_cap_yuan


def factor_ma_spread(stock: dict) -> float | None:
    """MA5 相对 MA20 发散(%)。"""
    closes = hist_closes(stock.get("历史行情") or [])
    if len(closes) < 20:
        return None
    return ma_spread_pct(closes)


def factor_spread_accel(stock: dict, *, lookback: int = 5) -> float | None:
    """近 lookback 日发散增量（百分点）。"""
    closes = hist_closes(stock.get("历史行情") or [])
    if len(closes) < 20 + lookback:
        return None
    now = ma_spread_pct(closes)
    prev = ma_spread_pct(closes[: -lookback] if lookback > 0 else closes)
    if now is None or prev is None:
        return None
    return now - prev


def factor_path_ratio(stock: dict, *, lookback: int = 60) -> float | None:
    """路径长度 / |净涨幅|；越小越顺滑（合成时取负）。"""
    closes = hist_closes(stock.get("历史行情") or [])
    if len(closes) < max(20, lookback // 2):
        return None
    window = closes[-min(len(closes), lookback) :]
    if len(window) < 10 or window[0] <= 0:
        return None
    path = 0.0
    for i in range(1, len(window)):
        if window[i - 1] > 0:
            path += abs(window[i] / window[i - 1] - 1.0)
    net = abs(window[-1] / window[0] - 1.0)
    if net < 1e-6:
        return None
    return path / net


def factor_fund_flow_ratio(stock: dict) -> float | None:
    """主力净流入 / 流通市值（小数）。"""
    net, _src = resolve_main_net_yuan(stock, mode="post_market_evening")
    mv = float_market_cap_yuan(stock)
    if net is None or mv is None or mv <= 0:
        return None
    return net / mv


def factor_ret_20d(stock: dict) -> float | None:
    """近 20 日收益(%)。"""
    closes = hist_closes(stock.get("历史行情") or [])
    if len(closes) < 21 or closes[-21] <= 0:
        return None
    return (closes[-1] / closes[-21] - 1.0) * 100


def factor_big_move_ratio(stock: dict, *, lookback: int = 30, thr: float = 5.0) -> float | None:
    """近 lookback 日单日涨幅≥thr% 的占比。"""
    chg = hist_daily_changes(stock.get("历史行情") or [])
    if len(chg) < max(10, lookback // 2):
        return None
    window = chg[-min(len(chg), lookback) :]
    if not window:
        return None
    hits = sum(1 for x in window if x >= thr)
    return hits / len(window)


# 合成时 path_ratio 越小越好 → 注册时用负向
FACTOR_SPECS: list[FactorSpec] = [
    FactorSpec("ma_spread", "MA5/MA20发散%", factor_ma_spread, default_weight=1.0),
    FactorSpec("spread_accel", "发散加速度", factor_spread_accel, default_weight=1.0),
    FactorSpec(
        "path_smooth",
        "路径顺滑(-path_ratio)",
        lambda s: (-x if (x := factor_path_ratio(s)) is not None else None),
        default_weight=0.8,
    ),
    FactorSpec("fund_flow_ratio", "主力净流入/市值", factor_fund_flow_ratio, default_weight=1.0),
    FactorSpec("ret_20d", "20日收益%", factor_ret_20d, default_weight=0.8),
    FactorSpec("big_move_ratio", "大涨日占比", factor_big_move_ratio, default_weight=0.6),
]


def compute_raw_factors(stock: dict, *, specs: list[FactorSpec] | None = None) -> dict[str, float]:
    """计算全部可用原始因子。"""
    out: dict[str, float] = {}
    for spec in specs or FACTOR_SPECS:
        try:
            v = spec.compute(stock)
        except Exception:
            v = None
        if v is not None and v == v:  # not NaN
            out[spec.name] = float(v)
    return out


def factor_names() -> list[str]:
    return [s.name for s in FACTOR_SPECS]


def default_weights() -> dict[str, float]:
    return {s.name: s.default_weight for s in FACTOR_SPECS}
