"""Markdown 视图生成。"""

from __future__ import annotations


def render_holding_md(rows: list[dict]) -> str:
    lines = ["# 持仓", ""]
    if not rows:
        lines.append("（空）")
        return "\n".join(lines) + "\n"
    for i, r in enumerate(rows, 1):
        name = r.get("股票名称", "")
        code = r.get("股票代码", "")
        qty = r.get("持仓股数", 0)
        price = r.get("买入价", "")
        reason = str(r.get("买入原因", "") or "").strip()
        suffix = f" [{reason}]" if reason else ""
        lines.append(f"{i}. {name}（{code}）{qty}股 买入价{price}{suffix}")
    return "\n".join(lines) + "\n"
