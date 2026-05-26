"""卖出信号：止损 / 去弱留强 / 趋势破位（满足任一即卖）。

优先级（同一次扫描内按 if-elif 只取第一个原因）
------------------------------------------------
1. 浮亏 <= stop_loss_pct → 止损
2. 持仓评分 < sell_threshold → 去弱留强
3. 跌破 MA20 → 趋势破位

T+1 限制在 execution/executor 中执行阶段检查。
"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine
from quant.signals.models import TradeSignal
from quant.store.state import get_holdings


def _price(stock: dict) -> float | None:
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    for key in ("最新", "最新价"):
        v = pk.get(key)
        if v is not None:
            try:
                p = float(v)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                continue
    return None


def generate_sell_signals(ctx: ScoreContext) -> list[TradeSignal]:
    engine = ScoringEngine()
    cfg = load_gates_config().get("sell") or {}
    sell_threshold = float(engine.config.get("sell_threshold", 45))
    stop_loss = float(cfg.get("stop_loss_pct", -5.0))
    signals: list[TradeSignal] = []

    for stock in get_holdings():
        code = str(stock.get("股票代码", "")).strip()
        if not code:
            continue
        # 用 API 持仓 enrich 数据覆盖磁盘持仓（含最新盘口/技术指标）
        enriched = stock
        for row in ctx.payload.get("持仓股") or []:
            if str(row.get("股票代码", "")).strip() == code:
                enriched = {**stock, **row}
                break
        price = _price(enriched)
        if price is None:
            continue
        try:
            buy_price = float(enriched.get("买入价", 0) or 0)
        except (TypeError, ValueError):
            buy_price = 0
        pnl_pct = (price - buy_price) / buy_price * 100 if buy_price > 0 else 0
        qty = int(enriched.get("持仓股数", 0) or 0)
        if qty < 100:
            continue

        score = engine.score_stock(ctx, enriched)
        strategy = str(enriched.get("战法", score.strategy))
        sell_type = ""
        reason = ""

        if pnl_pct <= stop_loss:
            sell_type = "止损"
            reason = f"浮亏{pnl_pct:.2f}%≤{stop_loss}%"
        elif score.total < sell_threshold:
            sell_type = "去弱留强"
            reason = f"持仓评分{score.total:.1f}<{sell_threshold}"
        elif cfg.get("ma20_break") and isinstance(enriched.get("技术指标"), dict):
            t = enriched["技术指标"]
            try:
                ma20 = float(t.get("MA20"))
                if price < ma20:
                    sell_type = "趋势破位"
                    reason = f"跌破MA20({ma20:.2f})"
            except (TypeError, ValueError):
                pass

        if not reason:
            continue
        signals.append(
            TradeSignal(
                action="卖出",
                code=code,
                name=str(enriched.get("股票名称", "")).strip(),
                price=price,
                quantity=qty,
                strategy=strategy,
                reason=reason,
                sell_type=sell_type,
            )
        )
    return signals
