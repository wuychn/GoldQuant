"""板块多因子评分（机构风格：RS / 资金 / 广度 /  persistence / 拥挤度）。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from quant.config import load_r2_config
from quant.domain.models import Lifecycle
from quant.sector.defensive import is_defensive_sector


def _f(row: dict, *keys: str) -> float | None:
    for k in keys:
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _clip01(x: float) -> float:
    return max(0.0, min(100.0, x))


def _rank_score(rank: int | None, *, top_n: int = 10) -> float:
    if rank is None or rank <= 0:
        return 40.0
    return _clip01(100.0 - (rank - 1) * (90.0 / max(top_n - 1, 1)))


@dataclass
class SectorFactorResult:
    rs_vs_index: float = 0.0
    fund_score: float = 50.0
    breadth_score: float = 50.0
    persistence_days: int = 0
    crowd_penalty: float = 0.0
    composite: float = 50.0
    lifecycle: Lifecycle = Lifecycle.SPROUT
    eligible: bool = False
    new_entry: bool = False
    note: str = ""


def breadth_from_row(row: dict) -> float | None:
    """板块内上涨广度 0～100。"""
    up = _f(row, "上涨家数")
    down = _f(row, "下跌家数")
    if up is not None and down is not None and (up + down) > 0:
        return _clip01(up / (up + down) * 100.0)

    members = _f(row, "公司家数")
    leader_chg = _f(row, "领涨股-涨跌幅")
    chg = _f(row, "行业-涨跌幅", "涨跌幅")
    if members and members > 5 and chg is not None and leader_chg is not None:
        # 概念榜无涨跌家数：龙头涨幅显著高于板块均值 → 宽度偏低
        spread = leader_chg - chg
        if spread > 4 and chg >= 3:
            return _clip01(35.0 - spread * 2)
        if spread < 1.5 and chg >= 2:
            return _clip01(55.0 + min(chg, 5) * 4)
    return None


def fund_score_from_row(
    row: dict,
    *,
    rank_fund: int | None,
    top_n: int,
    fund_momentum: float | None = None,
) -> float:
    net = _f(row, "净额", "净流入")
    rank_part = _rank_score(rank_fund, top_n=top_n)
    flow_part = 50.0
    if net is not None:
        if net > 0:
            flow_part = _clip01(55.0 + math.log1p(abs(net)) * 8.0)
        else:
            flow_part = _clip01(45.0 + net * 2.0)
    base = _clip01(rank_part * 0.45 + flow_part * 0.35)
    if fund_momentum is not None:
        base = _clip01(base * 0.55 + fund_momentum * 0.45)
    elif (mom := _f(row, "fund_momentum_score")) is not None:
        base = _clip01(base * 0.55 + mom * 0.45)
    return base


def compute_sector_factors(
    *,
    name: str,
    gain_row: dict,
    rank_gain: int | None,
    rank_fund: int | None,
    persistence_days: int,
    index_chg: float | None,
    top_n: int = 10,
    fund_mom: float | None = None,
) -> SectorFactorResult:
    cfg = load_r2_config().get("sector") or {}
    fcfg = cfg.get("factors") or {}
    weights = fcfg.get("weights") or {}
    w_rs = float(weights.get("rs", 0.25))
    w_fund = float(weights.get("fund", 0.25))
    w_breadth = float(weights.get("breadth", 0.20))
    w_persist = float(weights.get("persistence", 0.15))
    w_rank = float(weights.get("rank", 0.15))

    eligible_min = float(fcfg.get("eligible_min", 55))
    ban_max = float(fcfg.get("ban_max", 35))
    sprout_max = int((cfg.get("lifecycle") or {}).get("sprout_max_days", 2))

    chg = _f(gain_row, "行业-涨跌幅", "涨跌幅") or 0.0
    idx = index_chg if isinstance(index_chg, (int, float)) else 0.0
    rs = chg - idx
    rs_score = _clip01(50.0 + rs * 6.0)

    fund = fund_score_from_row(gain_row, rank_fund=rank_fund, top_n=top_n, fund_momentum=fund_mom)
    breadth_raw = breadth_from_row(gain_row)
    breadth = breadth_raw if breadth_raw is not None else 50.0

    persist_score = _clip01(40.0 + min(persistence_days, 8) * 7.5)
    rank_score = _rank_score(rank_gain, top_n=top_n)

    # 拥挤度：涨幅榜靠前 + 板块大涨 + 广度低 → 扣分（非一刀切禁入）
    crowd = 0.0
    if rank_gain is not None and rank_gain <= 3 and chg >= 5 and breadth < 50:
        crowd = _clip01((5 - min(breadth, 50) / 10) * 8 + (4 - rank_gain) * 3)
    if rank_gain is not None and rank_gain <= 3 and chg >= 5 and fund >= 70 and breadth >= 55:
        crowd *= 0.35  # 资金强 + 有广度 → 高潮仍可做，仅降 crowding

    composite = _clip01(
        rs_score * w_rs
        + fund * w_fund
        + breadth * w_breadth
        + persist_score * w_persist
        + rank_score * w_rank
        - crowd
    )

    defensive = is_defensive_sector(name)
    lifecycle = Lifecycle.SPROUT
    note_parts: list[str] = []

    if defensive and index_chg is not None and index_chg < 0 and chg > 0:
        lifecycle = Lifecycle.FADE
        note_parts.append("防御跷跷板")
    elif composite < ban_max:
        lifecycle = Lifecycle.FADE
        note_parts.append("因子综合偏弱")
    elif persistence_days <= sprout_max:
        lifecycle = Lifecycle.SPROUT
        note_parts.append("萌芽")
    elif rank_gain is not None and rank_gain <= 3 and chg >= 5 and breadth < 45 and fund < 65:
        lifecycle = Lifecycle.CLIMAX
        note_parts.append("龙头独涨/拥挤")
    elif persistence_days >= 3 and breadth >= 50:
        lifecycle = Lifecycle.SPREAD
        note_parts.append("扩散")
    elif persistence_days >= 3:
        lifecycle = Lifecycle.SPREAD
        note_parts.append("持续在榜")
    else:
        lifecycle = Lifecycle.SPROUT

    if defensive:
        note_parts.append("防御板块")

    eligible = composite >= eligible_min and lifecycle != Lifecycle.FADE and not defensive
    if lifecycle == Lifecycle.CLIMAX:
        # 高潮：资金或广度有一项强 → 仍可新进；否则仅禁止 tracking 新进
        eligible = eligible and (fund >= 68 or breadth >= 52 or composite >= eligible_min + 8)
        if not eligible:
            note_parts.append("高潮谨慎")

    new_entry = eligible

    return SectorFactorResult(
        rs_vs_index=rs,
        fund_score=fund,
        breadth_score=breadth,
        persistence_days=persistence_days,
        crowd_penalty=crowd,
        composite=composite,
        lifecycle=lifecycle,
        eligible=eligible,
        new_entry=new_entry,
        note=";".join(note_parts),
    )


def score_sector_row(
    *,
    name: str,
    gain_row: dict,
    rank_gain: int | None,
    rank_fund: int | None,
    persistence_days: int,
    index_chg: float | None,
    fund_momentum: float | None = None,
    top_n: int = 10,
) -> SectorFactorResult:
    return compute_sector_factors(
        name=name,
        gain_row=gain_row,
        rank_gain=rank_gain,
        rank_fund=rank_fund,
        persistence_days=persistence_days,
        index_chg=index_chg,
        top_n=top_n,
        fund_mom=fund_momentum,
    )
