"""分级滑点模型（含 ADV / 参与率冲击）。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from quant.execution.sim_rules import TradeSimConfig
from quant.data.quote import quote_change_pct


@dataclass
class SlippageContext:
    volatility_pct: float = 2.0
    amount: float = 0.0
    day_change_pct: float | None = None
    limit_pct: float = 9.9
    adv_amount: float = 0.0  # 日均成交额（元）
    participation: float = 0.0  # 本笔名义 / ADV


def effective_slippage_pct(cfg: TradeSimConfig, ctx: SlippageContext | None = None) -> float:
    """根据配置返回有效滑点比例。"""
    model = getattr(cfg, "slippage_model", "fixed") or "fixed"
    base = cfg.slippage_pct
    if model == "fixed" or ctx is None:
        return base

    slip = base
    model = getattr(cfg, "slippage_model", "fixed") or "fixed"

    if model in ("vol_scaled", "microstructure", "sqrt_law"):
        vol = max(0.5, ctx.volatility_pct)
        slip += base * (vol / 2.0 - 1.0) * 0.5
        if ctx.amount > 0:
            slip += min(0.002, 50000.0 / max(ctx.amount, 1.0) * 0.0001)

    # Square-root 冲击（Almgren-Chriss 风格）：σ * sqrt(participation)
    if model == "sqrt_law" and ctx.adv_amount > 0 and ctx.amount > 0:
        part = ctx.participation if ctx.participation > 0 else ctx.amount / ctx.adv_amount
        k = float(getattr(cfg, "slippage_sqrt_k", 0.5) or 0.5)
        slip += k * (max(0.5, ctx.volatility_pct) / 100.0) * math.sqrt(max(part, 1e-9))

    # ADV 冲击：参与率越高滑点越大（分档；sqrt_law 下作为补充底档）
    if model != "sqrt_law" and ctx.adv_amount > 0 and ctx.amount > 0:
        part = ctx.participation if ctx.participation > 0 else ctx.amount / ctx.adv_amount
        if part >= 0.2:
            slip += 0.003
        elif part >= 0.1:
            slip += 0.002
        elif part >= 0.05:
            slip += 0.001
        elif part >= 0.02:
            slip += 0.0005

    if model == "microstructure" and ctx.day_change_pct is not None:
        margin = ctx.limit_pct - abs(ctx.day_change_pct)
        if margin < 1.0:
            slip += 0.002
        elif margin < 2.0:
            slip += 0.001

    if model == "sqrt_law":
        max_slip = float(getattr(cfg, "slippage_sqrt_max_pct", 0.02) or 0.02)
    else:
        max_slip = float(getattr(cfg, "slippage_max_pct", 0.005) or 0.005)
    return min(max_slip, max(0.0, slip))


def slip_price_with_context(
    price: float,
    *,
    side: str,
    cfg: TradeSimConfig,
    stock: dict | None = None,
    code: str = "",
    quantity: int = 0,
    ctx: SlippageContext | None = None,
) -> float:
    from quant.execution.sim_rules import limit_pct
    from quant.pool.liquidity import avg_daily_amount_yi

    # 调用方(回测 broker)可预构造 ctx(ADV/波动已知),跳过 spot-style stock 适配;
    # live 仍走 stock 路径;二者均无 → 固定档(向后兼容)
    if ctx is None and getattr(cfg, "slippage_model", "fixed") != "fixed":
        chg = quote_change_pct(stock) if stock else None
        notional = price * max(quantity, 100)
        adv_yi = avg_daily_amount_yi(stock) if stock else None
        adv_amt = (adv_yi * 1e8) if adv_yi and adv_yi > 0 else 0.0
        part = (notional / adv_amt) if adv_amt > 0 else 0.0
        # 优先用 ATR/实现波动；缺历史时回退当日涨跌幅
        vol = 2.0
        if stock:
            from quant.portfolio.vol import stock_volatility_pct

            vol = stock_volatility_pct(stock, lookback=14, floor=0.5)
        elif chg is not None:
            vol = abs(chg)
        ctx = SlippageContext(
            volatility_pct=vol,
            amount=notional,
            day_change_pct=chg,
            limit_pct=limit_pct(code, cfg) if code else 9.9,
            adv_amount=adv_amt,
            participation=part,
        )
    slip = effective_slippage_pct(cfg, ctx)
    if side == "buy":
        return round(price * (1 + slip), 4)
    return round(price * (1 - slip), 4)
