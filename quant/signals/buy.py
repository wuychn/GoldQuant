"""买入信号：仅扫描「自选股」，不直接从人气榜开仓。

判定链（须全部通过）
--------------------
1. 未持仓
2. check_buy_gates（标的池、冷却、全局熔断）
3. 评分 >= buy_threshold
4. 盘前/盘中附加条件（gates.yml buy.pre_market / during_market）
5. calc_buy_quantity >= 100 股
"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.gates.rules import calc_buy_quantity, check_buy_gates
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine
from quant.signals.models import TradeSignal
from quant.store.state import get_holdings


def _price(stock: dict) -> float | None:
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    for key in ("最新", "今开", "最新价"):
        v = pk.get(key)
        if v is not None:
            try:
                p = float(v)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                continue
    return None


def _volume_ratio(stock: dict) -> float | None:
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    try:
        return float(pk.get("量比"))
    except (TypeError, ValueError):
        return None


def _gap_pct(stock: dict) -> float | None:
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    try:
        open_p = float(pk.get("今开"))
        prev = float(pk.get("昨收"))
        if prev <= 0:
            return None
        return (open_p - prev) / prev * 100
    except (TypeError, ValueError):
        return None


def generate_buy_signals(ctx: ScoreContext, *, mode: str) -> list[TradeSignal]:
    """mode: pre_market | during_market"""
    cfg = load_gates_config().get("buy") or {}
    engine = ScoringEngine()
    holdings = get_holdings()
    held = {str(h.get("股票代码", "")).strip() for h in holdings}
    watchlist = ctx.payload.get("自选股") or []
    signals: list[TradeSignal] = []

    for stock in watchlist:
        code = str(stock.get("股票代码", "")).strip()
        if not code or code in held:
            continue
        gates = check_buy_gates(stock, ctx)
        if not gates.passed:
            continue
        score = engine.score_stock(ctx, stock)
        if score.total < float(engine.config.get("buy_threshold", 72)):
            continue
        price = _price(stock)
        if price is None:
            continue

        if mode == "pre_market":
            pre_cfg = cfg.get("pre_market") or {}
            gap = _gap_pct(stock)
            vr = _volume_ratio(stock)
            if gap is None:
                continue
            if gap < float(pre_cfg.get("min_gap_pct", 1.0)) or gap > float(pre_cfg.get("max_gap_pct", 7.0)):
                continue
            if vr is not None and vr < float(pre_cfg.get("min_volume_ratio", 1.2)):
                continue
        elif mode == "during_market":
            dm_cfg = cfg.get("during_market") or {}
            pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
            try:
                chg = float(pk.get("涨幅", 0) or 0)
            except (TypeError, ValueError):
                chg = 0
            if chg >= float(dm_cfg.get("max_change_pct", 7.0)):
                continue
            if dm_cfg.get("require_above_avg", True):
                try:
                    last = float(pk.get("最新"))
                    avg = float(pk.get("均价"))
                    if last <= avg:
                        continue
                except (TypeError, ValueError):
                    continue

        qty = calc_buy_quantity({**stock, "战法": score.strategy}, ctx, price)
        if qty < 100:
            continue
        signals.append(
            TradeSignal(
                action="买入",
                code=code,
                name=str(stock.get("股票名称", "")).strip(),
                price=price,
                quantity=qty,
                strategy=score.strategy,
                reason=f"评分{score.total:.1f}；{gates.summary()}",
            )
        )
    return signals
