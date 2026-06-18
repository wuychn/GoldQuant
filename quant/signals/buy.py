"""买入原始信号：仅主升浪战法，仅自选股。"""

from __future__ import annotations

from dataclasses import dataclass

from quant.config import load_gates_config
from quant.constants import STRATEGY_NAME
from quant.gates.buy_policy import effective_buy_threshold, effective_max_change_pct
from quant.gates.rules import (
    allocate_buy_quantities_by_score,
    check_buy_gates,
    active_holding_count,
    position_limits,
)
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine
from quant.scoring.models import StockScore
from quant.signals.models import TradeSignal
from quant.store.intraday_fund_track import record_watchlist_fund_snapshots
from quant.store.state import get_holdings
from quant.scoring.tech_indicators import quote_last_price, quote_open_price
from quant.strategy.intraday import intraday_allows_buy
from quant.strategy.main_wave import detect_buy_setup
from quant.strategy.trend import trend_allows_buy


def _price(stock: dict) -> float | None:
    return quote_last_price(stock) or quote_open_price(stock)


@dataclass
class _BuyCandidate:
    score: StockScore
    code: str
    name: str
    price: float
    kind: str
    reason: str
    intra_note: str
    stock: dict


def _evaluate_buy_candidate(
    stock: dict,
    ctx: ScoreContext,
    *,
    mode: str,
    engine: ScoringEngine,
    mw_cfg: dict,
    buy_cfg: dict,
    held: set[str],
    buy_threshold: float,
) -> _BuyCandidate | None:
    """单只自选股买入评估；未通过任一门禁则 None。"""
    code = str(stock.get("股票代码", "")).strip()
    if not code or code in held:
        return None
    if not check_buy_gates(stock, ctx).passed:
        return None

    ok_trend, _ = trend_allows_buy(stock, mw_cfg)
    if not ok_trend:
        return None

    score = engine.score_stock(ctx, stock)
    if score.total < buy_threshold:
        return None

    ok, kind, reason = detect_buy_setup(stock, ctx, mw_cfg)
    if not ok:
        return None

    price = _price(stock)
    if price is None:
        return None

    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    try:
        chg = float(pk.get("涨幅", 0) or 0)
    except (TypeError, ValueError):
        chg = 0
    max_change_pct = effective_max_change_pct(
        buy_cfg,
        ctx.payload,
        buy_kind=kind,
        score=score.total,
    )
    if chg >= max_change_pct:
        return None

    if mode == "during_market":
        ok_intra, intra_note = intraday_allows_buy(stock, buy_cfg, buy_kind=kind)
        if not ok_intra:
            return None
    else:
        intra_note = ""

    return _BuyCandidate(
        score=score,
        code=code,
        name=str(stock.get("股票名称", "")).strip(),
        price=price,
        kind=kind,
        reason=reason,
        intra_note=intra_note,
        stock={**stock, "战法": STRATEGY_NAME},
    )


def _stock_from_payload(payload: dict, code: str) -> dict | None:
    for key in ("自选股", "持仓股"):
        for row in payload.get(key) or []:
            if isinstance(row, dict) and str(row.get("股票代码", "")).strip() == code:
                return row
    return None


def verify_buy_signal_still_valid(
    code: str,
    ctx: ScoreContext,
    *,
    mode: str = "during_market",
    signal_kind: str = "",
) -> bool:
    """持续确认成交前再验：结构 + 涨幅 + 分时轻量确认。"""
    stock = _stock_from_payload(ctx.payload, code)
    if not stock:
        return False
    mw_cfg = load_gates_config().get("main_wave") or {}
    buy_cfg = (load_gates_config().get("buy") or {}).get(
        "during_market" if mode == "during_market" else "pre_market"
    ) or {}
    if not trend_allows_buy(stock, mw_cfg)[0]:
        return False
    ok, kind, _ = detect_buy_setup(stock, ctx, mw_cfg)
    if not ok:
        return False
    if signal_kind and kind != signal_kind:
        return False
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    try:
        chg = float(pk.get("涨幅", 0) or 0)
    except (TypeError, ValueError):
        chg = 0
    max_change_pct = effective_max_change_pct(buy_cfg, ctx.payload, buy_kind=kind)
    if chg >= max_change_pct:
        return False
    if mode != "during_market":
        return True
    ok_intra, _ = intraday_allows_buy(
        stock,
        buy_cfg,
        buy_kind=kind,
        lightweight=True,
    )
    return ok_intra


def generate_buy_signals(ctx: ScoreContext, *, mode: str) -> list[TradeSignal]:
    """产生买入原始信号（未经三确认，勿直接 execute）。

    全量扫描自选股，按综合评分降序取前 N（N=剩余持仓空位），
    再按评分比例分配预算，避免列表顺序抢占名额。
    """
    if mode == "during_market":
        record_watchlist_fund_snapshots(ctx.payload.get("自选股") or [])

    mw_cfg = load_gates_config().get("main_wave") or {}
    buy_cfg = (load_gates_config().get("buy") or {}).get(
        "during_market" if mode == "during_market" else "pre_market"
    ) or {}
    engine = ScoringEngine()
    base_threshold = float(engine.config.get("buy_threshold", 72))
    buy_threshold = effective_buy_threshold(base_threshold, ctx.payload, buy_cfg)

    held = {str(h.get("股票代码", "")).strip() for h in get_holdings()}
    limits = position_limits(ctx)
    max_stocks = int(limits["max_stocks"])
    slots_left = max(0, max_stocks - active_holding_count())
    if slots_left <= 0:
        return []

    ranked: list[tuple[float, str, _BuyCandidate]] = []
    for stock in ctx.payload.get("自选股") or []:
        if not isinstance(stock, dict):
            continue
        candidate = _evaluate_buy_candidate(
            stock,
            ctx,
            mode=mode,
            engine=engine,
            mw_cfg=mw_cfg,
            buy_cfg=buy_cfg,
            held=held,
            buy_threshold=buy_threshold,
        )
        if candidate is None:
            continue
        ranked.append((candidate.score.total, candidate.code, candidate))

    ranked.sort(key=lambda x: (-x[0], x[1]))
    top = [c for _, _, c in ranked[:slots_left]]
    if not top:
        return []

    qty_map = allocate_buy_quantities_by_score(
        [(c.score.total, c.stock, c.price) for c in top],
        ctx,
    )

    signals: list[TradeSignal] = []
    for c in top:
        qty = qty_map.get(c.code, 0)
        if qty < 100:
            continue
        signals.append(
            TradeSignal(
                action="买入",
                code=c.code,
                name=c.name,
                price=c.price,
                quantity=qty,
                strategy=STRATEGY_NAME,
                reason=f"[{c.kind}]评分{c.score.total:.1f}；{c.reason}"
                + (f"；{c.intra_note}" if mode == "during_market" else ""),
                signal_kind=c.kind,
            )
        )
    return signals
