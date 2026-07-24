"""个股 alpha：结构 + 相对强度 + 板块共振。"""

from __future__ import annotations

from typing import Any

from quant.config import load_r2_config
from quant.scoring.context import ScoreContext
from quant.scoring.tech_indicators import quote_change_pct, quote_last_price
from quant.signals.structure import structure_score
from quant.strategy.momentum import momentum_score


def _clip01(x: float) -> float:
    return max(0.0, min(100.0, x))


def _sector_change_map(sectors: list[Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in sectors:
        name = getattr(s, "name", None) or (s.get("name") if isinstance(s, dict) else None)
        chg = getattr(s, "change_pct", None)
        if chg is None and isinstance(s, dict):
            chg = s.get("change_pct")
        if name and chg is not None:
            try:
                out[str(name)] = float(chg)
            except (TypeError, ValueError):
                continue
    return out


def compute_stock_alpha(
    stock: dict,
    *,
    ctx: ScoreContext | None = None,
    index_chg: float | None = None,
    sector_tags: list[str] | None = None,
    sector_changes: dict[str, float] | None = None,
) -> dict[str, float]:
    """返回 structure / rs_index / rs_sector / momentum / alpha 等子分。"""
    cfg = load_r2_config().get("stock_factors") or {}
    weights = cfg.get("weights") or {}
    w_struct = float(weights.get("structure", 0.45))
    w_rs_i = float(weights.get("rs_index", 0.20))
    w_rs_s = float(weights.get("rs_sector", 0.20))
    w_mom = float(weights.get("momentum", 0.15))

    struct = structure_score(stock)
    day = quote_change_pct(stock)
    idx = index_chg if isinstance(index_chg, (int, float)) else 0.0
    rs_i = 50.0
    if day is not None:
        rs_i = _clip01(50.0 + (day - idx) * 5.0)

    rs_s = 50.0
    tags = sector_tags or []
    smap = sector_changes or {}
    if tags and day is not None and smap:
        refs = [smap[t] for t in tags if t in smap]
        if refs:
            rs_s = _clip01(50.0 + (day - sum(refs) / len(refs)) * 6.0)

    mom = 50.0
    if ctx is not None:
        try:
            mom = _clip01(float(momentum_score(stock, ctx)))
        except Exception:
            mom = 50.0

    # 个股资金流（payload / 盘中快照）微调 momentum
    flow_boost = 0.0
    ff = stock.get("个股资金流") if isinstance(stock.get("个股资金流"), dict) else {}
    net = ff.get("大单流入", 0)
    outflow = ff.get("大单流出", 0)
    try:
        net_f = float(net or 0) - float(outflow or 0)
        if net_f > 0:
            flow_boost = min(8.0, net_f / 1e8 * 2)
        elif net_f < -5e7:
            flow_boost = -5.0
    except (TypeError, ValueError):
        pass
    mom = _clip01(mom + flow_boost)

    alpha = _clip01(
        struct * w_struct + rs_i * w_rs_i + rs_s * w_rs_s + mom * w_mom
    )
    price = quote_last_price(stock) or 0.0
    return {
        "structure_score": struct,
        "rs_index": rs_i,
        "rs_sector": rs_s,
        "momentum": mom,
        "alpha_score": alpha,
        "last_price": price,
    }
