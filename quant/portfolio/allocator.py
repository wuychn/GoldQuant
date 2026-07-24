"""仓位分配：评分 × 逆波动风险预算 + 成本/参与率约束。"""

from __future__ import annotations

from quant.config import load_quant_config
from quant.constants import STRATEGY_NAME
from quant.execution.sim_rules import calc_buy_cost, load_trade_sim_config
from quant.gates.rules import active_holding_count, per_stock_pct_at_full, position_limits
from quant.pool.liquidity import participation_notional_cap
from quant.portfolio.vol import stock_volatility_pct
from quant.scoring.context import ScoreContext
from quant.store.state import compute_holdings_market_value, get_cash, get_holdings, get_total_assets


def _portfolio_cfg() -> dict:
    return load_quant_config().get("portfolio") or {}


def _use_v2_allocator() -> bool:
    return bool(_portfolio_cfg().get("allocator_v2_enabled", True))


def _risk_budget_cfg() -> dict:
    return _portfolio_cfg().get("risk_budget") or {}


def _allocation_weights(
    candidates: list[tuple[float, dict, float]],
) -> list[float]:
    """综合评分与逆波动：w ∝ score^α / vol^(1-α) 的离散形式。

    blend=1 纯评分；blend=0 纯逆波动；默认 0.5。
    """
    rb = _risk_budget_cfg()
    enabled = bool(rb.get("enabled", True))
    blend = float(rb.get("score_vol_blend", 0.5))
    lookback = int(rb.get("vol_lookback", 14))
    vol_floor = float(rb.get("vol_floor_pct", 1.0))

    scores = [max(float(s), 1.0) for s, _, _ in candidates]
    if not enabled:
        return scores

    vols = [
        stock_volatility_pct(stock, lookback=lookback, floor=vol_floor)
        for _, stock, _ in candidates
    ]
    inv_vols = [1.0 / v for v in vols]
    # 归一化后再混合，避免量纲差异
    s_sum = sum(scores) or 1.0
    v_sum = sum(inv_vols) or 1.0
    s_n = [x / s_sum for x in scores]
    v_n = [x / v_sum for x in inv_vols]
    blend = max(0.0, min(1.0, blend))
    raw = [blend * a + (1.0 - blend) * b for a, b in zip(s_n, v_n)]
    # 映射回正权重
    return [max(w, 1e-6) for w in raw]


def allocate_buy_quantities_by_score(
    candidates: list[tuple[float, dict, float]],
    ctx: ScoreContext,
) -> dict[str, int]:
    """多候选按风险预算分配剩余仓位（100 股整数倍）。

    v2：预算用 calc_buy_cost 反推可买手数；可选 ADV 参与率封顶。
    """
    if not candidates:
        return {}

    if not _use_v2_allocator():
        from quant.gates.rules import allocate_buy_quantities_by_score as _legacy

        return _legacy(candidates, ctx)

    limits = position_limits(ctx)
    max_stocks = int(limits["max_stocks"])
    held = active_holding_count()
    if held >= max_stocks:
        return {}

    total_assets = get_total_assets()
    if total_assets <= 0:
        return {}

    current_mv = compute_holdings_market_value(get_holdings())
    total_cap = total_assets * float(limits["total_pct"]) / 100
    room = max(0.0, total_cap - current_mv)
    if room <= 0:
        return {}

    weights = _allocation_weights(candidates)
    weight_sum = sum(weights)
    if weight_sum <= 0:
        return {}

    remaining_cash = get_cash()
    sim = load_trade_sim_config()
    result: dict[str, int] = {}
    slots_left = max_stocks - held
    rb = _risk_budget_cfg()
    use_participation = bool(rb.get("participation_cap_enabled", True))

    ordered = sorted(
        zip(candidates, weights),
        key=lambda x: x[1],
        reverse=True,
    )[:slots_left]

    for (score, stock, price), w in ordered:
        if price <= 0 or remaining_cash < 100 * price:
            continue
        code = str(stock.get("股票代码", "")).strip()
        if not code:
            continue
        strategy = str(stock.get("战法", STRATEGY_NAME))
        budget = min(
            room * (w / weight_sum),
            total_assets * per_stock_pct_at_full(limits, strategy) / 100,
            remaining_cash,
        )
        if use_participation:
            cap = participation_notional_cap(stock)
            if cap is not None and cap > 0:
                budget = min(budget, cap)

        qty = int(budget / price / 100) * 100
        while qty >= 100:
            cost = calc_buy_cost(price, qty, code, sim, stock=stock)
            if cost.total <= remaining_cash + 1e-6:
                break
            qty -= 100
        if qty >= 100:
            result[code] = qty
            cost = calc_buy_cost(price, qty, code, sim, stock=stock)
            remaining_cash -= cost.total

    return result
