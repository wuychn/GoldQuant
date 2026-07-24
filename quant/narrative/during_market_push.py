"""R2 盘中飞书模板：大盘 + 持仓 + 作战池 + 信号/成交 + 三确认。"""

from __future__ import annotations

from quant.execution.executor import ExecutedTrade
from quant.gates.rules import format_position_control
from quant.io import state as state_io
from quant.io.quotes import build_stock_by_code
from quant.scoring.context import ScoreContext, index_change, profit_effect
from quant.scoring.tech_indicators import quote_last_price, stock_daily_change_pct
from quant.store.state import merge_payload_holdings
from quant.trading.models import TradeSignal


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _section(title: str) -> str:
    return f"\n{title}\n"


def _append_no_trade_notes(
    lines: list[str],
    *,
    ctx: ScoreContext | None,
    mode: str,
    raw_buy: list[TradeSignal],
    raw_sell: list[TradeSignal],
    audit: list[dict] | None,
) -> None:
    if ctx is None:
        return
    try:
        from quant.narrative.ops_context import sample_no_trade_reasons
    except ImportError:
        return
    buy_notes, sell_notes = sample_no_trade_reasons(
        ctx, mode=mode, raw_buy=raw_buy, raw_sell=raw_sell, audit=audit
    )
    for note in (buy_notes + sell_notes)[:4]:
        lines.append(f"  - {note}")


def build_during_market_push(
    payload: dict,
    *,
    timestamp: str,
    raw_buy: list[TradeSignal] | None = None,
    raw_sell: list[TradeSignal] | None = None,
    executable: list[TradeSignal] | None = None,
    executed: list[ExecutedTrade] | None = None,
    audit: list[dict] | None = None,
    rejected: dict[str, str] | None = None,
    ctx: ScoreContext | None = None,
    mode: str = "during_market",
) -> str:
    payload = merge_payload_holdings(dict(payload))
    pe = profit_effect(payload)
    idx = index_change(payload)
    lines = [
        f"智能盯盘 {timestamp}",
        f"上证 {_fmt_pct(idx)} | 涨{pe.get('上涨', '—')}/跌{pe.get('下跌', '—')} | 涨停{pe.get('涨停', '—')}",
        format_position_control(payload),
    ]

    holdings = [h for h in (payload.get("持仓股") or []) if isinstance(h, dict)]
    lines.append(_section("持仓"))
    if not holdings:
        lines.append("· 当前空仓，无持仓股")
    else:
        for h in holdings:
            code = str(h.get("股票代码", "")).strip()
            px = quote_last_price(h) or float(h.get("买入价") or 0)
            buy = float(h.get("买入价") or 0)
            qty = int(h.get("持仓股数", 0) or 0)
            pnl = (px - buy) / buy * 100 if buy > 0 and px > 0 else None
            lines.append(
                f"· {h.get('股票名称', code)}({code}) {qty}股 "
                f"成本{buy:.2f} 浮盈{_fmt_pct(pnl)} "
                f"买入{h.get('买入时间', h.get('买入日期', '—'))}"
            )

    combat = state_io.load_combat()
    if combat:
        lines.append(_section(f"作战池({len(combat)}) · 因子分=个股alpha(0~100，≥65可升档)"))
        stock_map = build_stock_by_code(payload)
        for m in sorted(combat, key=lambda x: -x.alpha_score)[:8]:
            row = stock_map.get(m.code, {})
            chg = stock_daily_change_pct(row)
            chg_s = _fmt_pct(chg) if chg is not None else ("—(无实时)" if row.get("quote_stale") else "—")
            lines.append(
                f"· {m.name}({m.code}) 因子{m.alpha_score:.0f} {chg_s} "
                f"[{','.join(m.sector_tags[:2])}]"
            )

    raw_buy = raw_buy or []
    raw_sell = raw_sell or []
    executable = executable or []
    executed = executed or []

    lines.append(_section("买卖信号"))
    has_signal = False
    for sig in raw_buy:
        has_signal = True
        lines.append(f"· 买 {sig.name}({sig.code}) {sig.signal_kind} {sig.reason[:80]}")
    for sig in raw_sell:
        has_signal = True
        lines.append(f"· 卖 {sig.name}({sig.code}) {sig.sell_type} {sig.reason[:80]}")
    for sig in executable:
        has_signal = True
        lines.append(f"· 可执行 {sig.action} {sig.name}({sig.code})")
    for ex in executed:
        has_signal = True
        sig = ex.signal
        lines.append(
            f"· 成交 {sig.action} {sig.name}({sig.code}) "
            f"{ex.quantity}股 @{ex.fill_price:.2f} 盈亏{ex.pnl:.0f}"
        )
    if not has_signal:
        lines.append("· 本轮无原始买卖信号（作战池未触发主升买点或分时/风控未过）")
        _append_no_trade_notes(
            lines, ctx=ctx, mode=mode, raw_buy=raw_buy, raw_sell=raw_sell, audit=audit
        )

    if audit:
        pending = [a for a in audit if not a.get("可执行") and a.get("状态")]
        if pending:
            lines.append(_section("三确认进度"))
            for a in pending[:6]:
                lines.append(f"· {a.get('代码', '')} {a.get('状态', '')}")

    if rejected:
        lines.append(_section("未成交"))
        for code, reason in list(rejected.items())[:5]:
            lines.append(f"· {code} {reason}")

    return "\n".join(lines)
