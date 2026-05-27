"""Markdown 视图生成。"""

from __future__ import annotations

from quant.constants import STRATEGY_NAME


def render_optional_md(rows: list[dict]) -> str:
    lines = ["# 自选股", ""]
    if not rows:
        lines.append("（空）")
        return "\n".join(lines) + "\n"
    for i, r in enumerate(rows, 1):
        name = r.get("股票名称", "")
        code = r.get("股票代码", "")
        tag = r.get("战法", STRATEGY_NAME)
        score = r.get("评分")
        reason = r.get("加入自选原因", "")
        score_part = f" 评分{score}" if score is not None else ""
        lines.append(f"{i}. {name}（{code}）[{tag}]{score_part}")
        if reason:
            lines.append(f"   {reason}")
    return "\n".join(lines) + "\n"


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
        tag = r.get("战法", "")
        lines.append(f"{i}. {name}（{code}）{qty}股 买入价{price} [{tag}]")
    return "\n".join(lines) + "\n"
