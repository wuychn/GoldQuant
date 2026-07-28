"""基本面族：估值/盈利/成长（PIT 快照 + extras 注入）。"""

from __future__ import annotations

from quant.factors.library.base import BarSeries, FactorDef


def _fundamental(bars: BarSeries, as_of: str, key: str) -> float | None:
    extras = getattr(bars, "extras", None) or {}
    v = extras.get(key)
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f and abs(f) < 1e8 else None
    except (TypeError, ValueError):
        return None


def ep_ttm_z(bars: BarSeries, as_of: str) -> float | None:
    """盈利收益率 1/PE（快照 ep_ttm 或 pe_ttm 倒数）。"""
    v = _fundamental(bars, as_of, "ep_ttm")
    if v is not None:
        return v
    pe = _fundamental(bars, as_of, "pe_ttm")
    if pe is None or pe <= 0:
        return None
    return 1.0 / pe


def bp_z(bars: BarSeries, as_of: str) -> float | None:
    """账面市值比 1/PB（价值代理）。"""
    v = _fundamental(bars, as_of, "bp")
    if v is not None:
        return v
    pb = _fundamental(bars, as_of, "pb")
    if pb is None or pb <= 0:
        return None
    return 1.0 / pb


def roe_z(bars: BarSeries, as_of: str) -> float | None:
    return _fundamental(bars, as_of, "roe")


def rev_yoy_z(bars: BarSeries, as_of: str) -> float | None:
    return _fundamental(bars, as_of, "rev_yoy")


FUNDAMENTAL_FACTORS: list[FactorDef] = [
    FactorDef("ep_ttm", "盈利收益率1/PE", ep_ttm_z, direction=1.0, default_weight=0.8),
    FactorDef("bp", "账面市值比1/PB", bp_z, direction=1.0, default_weight=0.6),
    FactorDef("roe", "净资产收益率", roe_z, direction=1.0, default_weight=1.0),
    FactorDef("rev_yoy", "营收同比增长", rev_yoy_z, direction=1.0, default_weight=0.8),
]
