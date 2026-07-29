"""盘中因子族：基于 spot_em 实时快照的短周期量价信号。

与日频因子（``library/*`` 吃 ``BarSeries`` 日 K）并列，盘中因子吃 ``SpotRow``（实时快照行），
用于 T+1 盘中对作战池择时触发买入。截面 z-score 合成 intraday_alpha
（见 ``quant.factors.compose.compose_intraday_alpha``）。

设计原则：因子值均来自 ``fetch_spot_em`` 实时可得字段（最新价/今开/昨收/涨跌幅/量比/换手率/涨速），
不做技术分析式画线，保持与日频多因子同样的工程化方式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class SpotRow:
    """spot_em 单行映射（实时快照）。"""

    code: str
    last: float  # 最新价
    open: float  # 今开
    pre_close: float  # 昨收
    pct: float  # 涨跌幅(%)
    volume: float  # 今日截至量
    amount: float  # 今日截至额
    vol_ratio: float  # 量比（>1 放量）
    turnover: float  # 换手率(%)
    speed: float  # 涨速(%)


IntradayFactorFn = Callable[[SpotRow], float | None]


@dataclass(frozen=True)
class IntradayFactorDef:
    name: str
    description: str
    compute: IntradayFactorFn
    direction: float = 1.0  # +1 越大越看多
    default_weight: float = 1.0


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def intraday_strength(s: SpotRow) -> float | None:
    """盘中走强 = (最新 - 今开) / 今开。正=开盘后上行。"""
    if s.open <= 0:
        return None
    return (s.last - s.open) / s.open


def volume_ratio(s: SpotRow) -> float | None:
    """量比（spot_em 直接给，>1 放量）。"""
    return s.vol_ratio if s.vol_ratio > 0 else None


def speed_factor(s: SpotRow) -> float | None:
    """涨速（spot_em 给，盘中短期加速）。"""
    return s.speed


def day_change(s: SpotRow) -> float | None:
    """当日涨跌幅(%)。"""
    return s.pct


def turnover_high(s: SpotRow) -> float | None:
    """换手率(%)。"""
    return s.turnover


INTRADAY_FACTORS: list[IntradayFactorDef] = [
    IntradayFactorDef("intraday_strength", "盘中走强(最新vs今开)", intraday_strength, 1.0, 1.0),
    IntradayFactorDef("volume_ratio", "量比(放量)", volume_ratio, 1.0, 1.0),
    IntradayFactorDef("speed", "涨速(盘中加速)", speed_factor, 1.0, 0.8),
    IntradayFactorDef("day_change", "当日涨跌幅", day_change, 1.0, 0.6),
    IntradayFactorDef("turnover", "换手率", turnover_high, 1.0, 0.5),
]


def spot_row_from_dict(d: dict) -> SpotRow | None:
    """从 spot_em normalize 后的行 dict 构造 ``SpotRow``；缺关键价返回 None。"""
    code = str(d.get("code") or "").strip()
    last = _to_float(d.get("close"))
    if not code or last is None or last <= 0:
        return None
    return SpotRow(
        code=code,
        last=last,
        open=_to_float(d.get("open")) or last,
        pre_close=_to_float(d.get("pre_close")) or last,
        pct=_to_float(d.get("pct")) or 0.0,
        volume=_to_float(d.get("volume")) or 0.0,
        amount=_to_float(d.get("amount")) or 0.0,
        vol_ratio=_to_float(d.get("vol_ratio")) or 0.0,
        turnover=_to_float(d.get("turnover_rate")) or 0.0,
        speed=_to_float(d.get("speed")) or 0.0,
    )


def spot_row_from_daily(
    code: str,
    row: dict,
    prev_close: float | None,
    *,
    vol_ratio: float = 0.0,
) -> SpotRow | None:
    """从**日 K 行**构造 ``SpotRow``（盘中择时日频代理回测用）。

    忠实还原：last=close、open=今开、pre_close=昨收、pct、volume、amount、turnover；
    ``vol_ratio`` 由调用方从历史算（今日量 / 近 5 日均量）后传入。
    **speed 代理**：tick 级涨速日频不可得，用 ``(close-open)/open*100``（%）近似
    收盘相对开盘的日内加速，与 live ``spot_em.speed`` 同向但不等价。
    """
    last = _to_float(row.get("close"))
    if last is None or last <= 0:
        return None
    open_ = _to_float(row.get("open")) or last
    pre = prev_close if (prev_close and prev_close > 0) else (_to_float(row.get("pre_close")) or last)
    pct = (last / pre - 1.0) * 100.0 if pre > 0 else 0.0
    speed_proxy = ((last - open_) / open_ * 100.0) if open_ > 0 else 0.0
    return SpotRow(
        code=str(code),
        last=last,
        open=open_,
        pre_close=pre,
        pct=pct,
        volume=_to_float(row.get("volume")) or 0.0,
        amount=_to_float(row.get("amount")) or 0.0,
        vol_ratio=float(vol_ratio or 0.0),
        turnover=_to_float(row.get("turnover_rate")) or 0.0,
        speed=speed_proxy,
    )
