"""晚间研究简报：板块资金 + 作战池个股 AKShare 研报/新闻。"""

from __future__ import annotations

import json
from typing import Any

from quant.data.akshare_client import AkShareClient
from quant.factors.fund_momentum import fund_momentum_score
from quant.io import state as state_io
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY
from quant.store.paths import quant_home
from quant.timeutil import cn_date_str


def _fmt_yi(v: object) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(x) >= 1e8:
        return f"{x/1e8:.2f}亿"
    if abs(x) >= 1e4:
        return f"{x/1e4:.0f}万"
    return f"{x:.0f}"


def build_pool_research_snippet(*, limit: int = 3) -> str:
    """作战池 Top 个股 AKShare 新闻/研报一行摘要。"""
    client = AkShareClient()
    combat = sorted(state_io.load_combat(), key=lambda m: -m.alpha_score)[:limit]
    if not combat:
        return ""
    lines = ["📑 作战池研究速览"]
    for m in combat:
        code = m.code
        sym = code.split(".")[0] if "." in code else code[-6:]
        news = client.stock_news(sym, limit=2)
        reports = client.stock_research_reports(sym, limit=1)
        parts = [f"· {m.name}({code}) α{m.alpha_score:.0f}"]
        if reports:
            r0 = reports[0]
            title = str(r0.get("报告名称") or r0.get("标题") or r0.get("研报标题") or "")[:40]
            if title:
                parts.append(f"研报:{title}")
        if news:
            n0 = news[0]
            title = str(n0.get("新闻标题") or n0.get("标题") or "")[:36]
            if title:
                parts.append(f"新闻:{title}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def build_evening_research(
    payload: dict,
    *,
    sectors: list[Any] | None = None,
    date_str: str | None = None,
) -> str:
    """生成晚间研究段并落盘 derived/research_report.json。"""
    ds = date_str or cn_date_str()
    client = AkShareClient()
    lines = ["📊 机构研究简报", ""]

    # 大盘资金
    mkt = client.market_fund_flow_summary(date_str=ds)
    latest = mkt.get("latest") or {}
    if latest:
        main = latest.get("主力净流入-净额") or latest.get("主力净流入") or latest.get("净流入")
        lines.append(f"大盘主力净流入 {_fmt_yi(main)}")

    # 板块资金 Top
    lines.append("")
    lines.append("行业资金（5日动量 Top5）:")
    industry_scores: list[tuple[float, str]] = []
    for row in client.sector_fund_flow_rank("5日", sector_type="行业资金流", date_str=ds)[:30]:
        name = str(row.get("名称") or row.get("行业") or "").strip()
        if name:
            industry_scores.append((fund_momentum_score(name, BOARD_INDUSTRY, date_str=ds), name))
    for sc, name in sorted(industry_scores, reverse=True)[:5]:
        lines.append(f"· {name} 动量{sc:.0f}")

    lines.append("")
    lines.append("概念资金（5日动量 Top5）:")
    concept_scores: list[tuple[float, str]] = []
    for row in client.sector_fund_flow_rank("5日", sector_type="概念资金流", date_str=ds)[:30]:
        name = str(row.get("名称") or row.get("行业") or "").strip()
        if name:
            concept_scores.append((fund_momentum_score(name, BOARD_CONCEPT, date_str=ds), name))
    for sc, name in sorted(concept_scores, reverse=True)[:5]:
        lines.append(f"· {name} 动量{sc:.0f}")

    pool_snip = build_pool_research_snippet(limit=4)
    if pool_snip:
        lines.append("")
        lines.append(pool_snip)

    body = "\n".join(lines)
    derived = quant_home() / "daily" / ds / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    (derived / "research_report.json").write_text(
        json.dumps({"date": ds, "body": body}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return body
