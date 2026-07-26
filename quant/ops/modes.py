"""r3 运维模式正文生成（无专家评分 / 无逐笔信号）。"""

from __future__ import annotations

import json
from typing import Any

from quant.data_fetch import fetch_mode, unwrap_payload
from quant.ops.paper_view import paper_account_lines, paper_block, paper_holdings_lines
from quant.push.format import kv_line, money, section
from quant.store.state import write_news_summary


def _index_lines(payload: dict) -> list[str]:
    lines: list[str] = []
    for key in ("指数", "大盘", "indices", "index"):
        block = payload.get(key)
        if not block:
            continue
        if isinstance(block, list):
            for row in block[:6]:
                if not isinstance(row, dict):
                    continue
                name = row.get("名称") or row.get("name") or row.get("代码") or ""
                chg = row.get("涨跌幅") or row.get("change_pct") or row.get("涨幅")
                px = row.get("最新") or row.get("close") or row.get("最新价")
                if name:
                    lines.append(f"{name} {px} ({chg}%)" if chg is not None else f"{name} {px}")
        elif isinstance(block, dict):
            for name, row in list(block.items())[:6]:
                if isinstance(row, dict):
                    chg = row.get("涨跌幅") or row.get("change_pct")
                    lines.append(f"{name} {chg}%")
                else:
                    lines.append(f"{name} {row}")
        break
    return lines


def _top_movers(payload: dict, *, key_candidates: tuple[str, ...], limit: int = 5) -> list[str]:
    for key in key_candidates:
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        out: list[str] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            code = row.get("股票代码") or row.get("code") or ""
            name = row.get("股票名称") or row.get("name") or ""
            chg = row.get("涨跌幅") or row.get("change_pct")
            out.append(f"{code} {name} {chg}%")
        if out:
            return out
    return []


def build_news_body(raw: dict) -> str:
    from quant.narrative.llm import call_llm
    from quant.narrative.prompts import prompt_news

    payload = unwrap_payload(raw)
    if isinstance(payload, dict) and "news" in payload:
        news_list = payload["news"]
    elif isinstance(payload, list):
        news_list = payload
    else:
        news_list = [payload]
    user = json.dumps({"news": news_list}, ensure_ascii=False)[:140000]
    summary = call_llm(prompt_news(), user, max_tokens=2500)
    # 落摘要供盘前引用
    if "综合解读" in summary:
        tail = summary.split("综合解读", 1)[-1]
        write_news_summary(f"综合解读{tail.strip()[:800]}")
    # 推送只保留精简版：截到合理长度
    text = summary.strip()
    if len(text) > 1800:
        text = text[:1800] + "…"
    return text


def build_pre_market_body(raw: dict) -> str:
    payload = unwrap_payload(raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    parts = [
        section("指数", _index_lines(payload)),
        paper_block("纸面账户"),
        section(
            "关注",
            _top_movers(payload, key_candidates=("涨幅榜", "自选股", "热门股", "人气榜")),
        ),
        section(
            "提示",
            [
                "今日决策与纸面成交由日频任务自动执行",
                "以目标组合差额交易，出场看 ATR/硬止损/破位/时间",
            ],
        ),
    ]
    from quant.store.state import read_news_summary

    news = read_news_summary()
    if news:
        parts.insert(0, section("新闻要点", [news[:400]]))
    return "\n\n".join(p for p in parts if p)


def build_during_body(raw: dict) -> str:
    payload = unwrap_payload(raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    parts = [
        section("指数", _index_lines(payload)),
        paper_block("纸面持仓"),
        section(
            "异动",
            _top_movers(payload, key_candidates=("涨幅榜", "自选股", "持仓股"), limit=6),
        ),
    ]
    return "\n\n".join(p for p in parts if p)


def build_lunch_body(raw: dict) -> str:
    payload = unwrap_payload(raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    parts = [
        section("午前指数", _index_lines(payload)),
        paper_block("纸面账户"),
        section("提示", ["午间不交易；关注下午是否触发止损/破位"]),
    ]
    return "\n\n".join(p for p in parts if p)


def build_evening_body(raw: dict) -> str:
    payload = unwrap_payload(raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    # 权益曲线最近一行
    eq_lines: list[str] = []
    try:
        from quant.decision.paper_execute import paper_home_context
        from quant.store.paths import state_file

        with paper_home_context():
            path = state_file("equity.jsonl")
            if path.is_file():
                lines = path.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    last = json.loads(lines[-1])
                    eq_lines = [
                        f"日期 {last.get('date')}",
                        f"总资产 {money(last.get('总资产'))}",
                        f"当日成交 {last.get('n_trades')} 笔 | 持仓 {last.get('n_holdings')} 只",
                    ]
    except Exception:
        pass
    parts = [
        section("收盘指数", _index_lines(payload)),
        section("纸面绩效", eq_lines or paper_account_lines()),
        section("持仓", paper_holdings_lines(10)),
        section("明日", ["收盘后跑日决策，按目标组合再平衡并更新权益"]),
    ]
    return "\n\n".join(p for p in parts if p)


def build_decision_push_body(payload: dict[str, Any]) -> str:
    """日决策 + 纸面成交摘要推送正文。"""
    actions = payload.get("actions") or []
    paper = payload.get("paper") or {}
    acc = paper.get("account") or {}
    executed = paper.get("executed") or []
    lines_act = []
    for a in actions[:12]:
        lines_act.append(
            f"{a.get('side')} {a.get('code')} "
            f"目标{float(a.get('target_weight') or 0)*100:.1f}% "
            f"Δ{float(a.get('delta_weight') or 0)*100:+.1f}% "
            f"{a.get('reason') or ''}"
        )
    lines_fill = []
    for e in executed[:12]:
        lines_fill.append(
            f"{e.get('action')} {e.get('code')} x{e.get('qty')} @{e.get('fill')} "
            f"pnl={e.get('pnl')} {e.get('reason') or ''}"
        )
    parts = [
        section("账户", [
            f"总资产 {money(acc.get('总资产'))}",
            f"现金 {money(acc.get('可用资金'))} | 市值 {money(acc.get('持仓市值'))}",
            f"成交 {paper.get('n_executed', 0)} 笔",
        ]),
        section("指令", lines_act),
        section("成交", lines_fill),
        section("持仓", [
            f"{h.get('code')} {h.get('name')} {h.get('shares')}股 @{h.get('cost')}"
            for h in (paper.get("holdings") or [])[:8]
        ]),
    ]
    return "\n\n".join(p for p in parts if p)
