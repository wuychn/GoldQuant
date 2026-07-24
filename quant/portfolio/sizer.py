"""机构式仓位：风险预算 + 按 alpha 加权分配。"""

from __future__ import annotations

from quant.config import load_r2_config
from quant.gates.rules import active_holding_count, allocate_buy_quantities_by_score, position_limits
from quant.scoring.context import ScoreContext
from quant.store.state import get_total_assets


def compute_buy_quantity(
    stock: dict,
    ctx: ScoreContext,
    price: float,
    *,
    alpha_score: float = 70.0,
) -> int:
    """按 regime 总仓位上限、剩余空位与 alpha 权重计算买入股数。"""
    if price <= 0:
        return 0
    cfg = load_r2_config().get("portfolio") or {}
    limits = position_limits(ctx)
    held = active_holding_count()
    if held >= int(limits.get("max_stocks", 3)):
        return 0

    total_assets = get_total_assets()
    if total_assets <= 0:
        total_assets = float(cfg.get("default_assets", 500_000))

    per_max_pct = float(cfg.get("per_stock_max_pct", 0.18))
    max_by_name = total_assets * per_max_pct

    weight = max(1.0, float(alpha_score))
    qty_map = allocate_buy_quantities_by_score(
        [(weight, {**stock, "战法": "R2主升浪"}, price)],
        ctx,
    )
    code = str(stock.get("股票代码", "")).strip()
    qty = qty_map.get(code, 0)
    if qty <= 0:
        return 0
    cap_qty = int(max_by_name / price / 100) * 100
    if cap_qty >= 100:
        qty = min(qty, cap_qty)
    return max(qty, 0) if qty >= 100 else 0
