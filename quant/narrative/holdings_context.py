"""持仓摘要：供 engine_brief / 盈亏参考使用，以 state/holding.jsonl 为权威来源。"""

from __future__ import annotations

from quant.narrative.stock_lines import daily_change_suffix, name_code_label
from quant.scoring.tech_indicators import stock_daily_change_pct
from quant.store.state import get_holdings, resolve_payload_holdings


def format_holdings_performance(payload: dict | None = None) -> str:
    """持仓股表现：名称、代码、当日涨跌（供午间/晚间第三节）。"""
    rows = resolve_payload_holdings(payload)
    if not rows:
        return "暂无持仓。"
    lines: list[str] = []
    for h in rows:
        lines.append(f"· {name_code_label(h)} {daily_change_suffix(h)}")
    return "\n".join(lines)


def format_holdings_summary(payload: dict | None = None) -> str:
    """当前持仓确定性摘要（勿与 payload 空列表混淆为空仓）。"""
    rows = resolve_payload_holdings(payload)
    state_rows = get_holdings()
    if not rows:
        return "当前持仓：无（state/holding.jsonl 为空）。"

    lines = [f"当前持仓（共{len(rows)}只）："]
    for h in rows:
        qty = int(h.get("持仓股数", 0) or 0)
        buy = h.get("买入价", "—")
        chg = stock_daily_change_pct(h)
        chg_s = f" 当日{chg:+.2f}%" if chg is not None else ""
        lines.append(f"· {name_code_label(h)} {qty}股 买入价{buy}{chg_s}")

    payload_n = len((payload or {}).get("持仓股") or [])
    if state_rows and payload_n != len(state_rows):
        lines.append(
            f"（state 共{len(state_rows)}只；本次行情 payload 原仅 {payload_n} 只，已合并 state 持仓）"
        )
    return "\n".join(lines)


def holdings_for_pnl(payload: dict | None = None) -> list[dict]:
    """盈亏估算用持仓：优先 payload  enriched 行，缺失时回退 state。"""
    rows = resolve_payload_holdings(payload)
    if rows:
        return rows
    return get_holdings()
