"""纸面账户摘要（供盘前/盯盘/复盘推送）。"""

from __future__ import annotations

from quant.decision.paper_execute import paper_home_context
from quant.push.format import money, section
from quant.store.state import get_account, get_holdings


def paper_account_lines() -> list[str]:
    with paper_home_context():
        acc = get_account()
        holdings = get_holdings()
    lines = [
        f"总资产 {money(acc.get('总资产'))}",
        f"现金 {money(acc.get('可用资金'))} | 市值 {money(acc.get('持仓市值'))}",
        f"累计盈亏 {money(acc.get('累计已实现盈亏'))} | 持仓 {len(holdings)} 只",
    ]
    return lines


def paper_holdings_lines(limit: int = 8) -> list[str]:
    with paper_home_context():
        holdings = get_holdings()
    rows: list[str] = []
    for h in holdings[:limit]:
        code = h.get("股票代码")
        name = h.get("股票名称") or ""
        qty = h.get("持仓股数")
        cost = h.get("买入价")
        reason = str(h.get("买入原因") or "")[:40]
        rows.append(f"{code} {name} {qty}股 @{cost} {reason}".strip())
    if len(holdings) > limit:
        rows.append(f"…共 {len(holdings)} 只")
    return rows


def paper_block(title: str = "纸面账户") -> str:
    return section(title, paper_account_lines() + paper_holdings_lines())
