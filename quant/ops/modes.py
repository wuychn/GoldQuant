"""r3 运维模式正文生成（无专家评分 / 无逐笔信号）。"""

from __future__ import annotations

import json
from typing import Any

from quant.data_fetch import unwrap_payload
from quant.ops.paper_view import paper_account_lines, paper_block, paper_holdings_lines
from quant.push.format import (
    ICON_ACCOUNT,
    ICON_FILL,
    ICON_HOLD,
    ICON_MARKET,
    ICON_NEWS,
    ICON_TIP,
    ICON_WATCH,
    INDEX_LABELS,
    emoji_for_pct,
    fmt_pct,
    fmt_price,
    icon_section,
    money,
)
from quant.store.state import write_news_summary


def _first(row: dict, *keys: str):
    """取 row 中首个非空字段（保留 0）。"""
    for k in keys:
        v = row.get(k)
        if v is not None and v != "":
            return v
    return None


def _index_lines(payload: dict) -> list[str]:
    lines: list[str] = []
    for key in ("大盘指数", "指数", "大盘", "indices", "index"):
        block = payload.get(key)
        if not block:
            continue
        if isinstance(block, list):
            for row in block[:6]:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("代码") or row.get("code") or "")
                name = _first(row, "名称", "name") or code
                label = INDEX_LABELS.get(code) or name
                chg = _first(row, "涨跌幅", "change_pct", "涨幅")
                px = _first(row, "最新", "close", "最新价")
                if label:
                    lines.append(
                        f"{label} {fmt_price(px)} {emoji_for_pct(chg)}{fmt_pct(chg, short=True)}"
                    )
        elif isinstance(block, dict):
            for name, row in list(block.items())[:6]:
                if isinstance(row, dict):
                    chg = _first(row, "涨跌幅", "change_pct")
                    lines.append(f"{name} {emoji_for_pct(chg)}{fmt_pct(chg, short=True)}")
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
            chg = _first(row, "涨跌幅", "change_pct")
            out.append(f"{code} {name} {emoji_for_pct(chg)}{fmt_pct(chg, short=True)}")
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
        icon_section(ICON_MARKET, "指数", _index_lines(payload)),
        paper_block("纸面账户"),
        icon_section(
            ICON_WATCH,
            "关注",
            _top_movers(payload, key_candidates=("涨幅榜", "自选股", "热门股", "人气榜")),
        ),
        icon_section(
            ICON_TIP,
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
        parts.insert(0, icon_section(ICON_NEWS, "新闻要点", [news[:400]]))
    return "\n\n".join(p for p in parts if p)


def build_during_body(raw: dict) -> str:
    payload = unwrap_payload(raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    session = payload.pop("_session", None)
    if session is None:
        raise ValueError("during_market payload 缺少 _session（须先 run_intraday_session）")
    degraded = payload.get("_degraded") or []
    parts = [
        icon_section(ICON_MARKET, "指数", _index_lines(payload)),
        paper_block("纸面持仓"),
        icon_section(
            ICON_WATCH,
            "异动",
            _top_movers(payload, key_candidates=("涨幅榜", "自选股", "持仓股"), limit=6),
        ),
    ]
    if degraded:
        parts.append(icon_section(ICON_TIP, "数据缺失", [f"⚠️ {', '.join(degraded)} 缺失"]))
    sell_block = session.get("sell_block") or ""
    if sell_block:
        parts.append(sell_block)
    buy_block = session.get("buy_block") or ""
    if buy_block:
        parts.append(buy_block)
    return "\n\n".join(p for p in parts if p)


def build_lunch_body(raw: dict) -> str:
    payload = unwrap_payload(raw) if isinstance(raw, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    parts = [
        icon_section(ICON_MARKET, "午前指数", _index_lines(payload)),
        paper_block("纸面账户"),
        icon_section(ICON_TIP, "提示", ["午间不交易；关注下午是否触发止损/破位"]),
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
        icon_section(ICON_MARKET, "收盘指数", _index_lines(payload)),
        icon_section(ICON_ACCOUNT, "纸面绩效", eq_lines or paper_account_lines()),
        icon_section(ICON_HOLD, "持仓", paper_holdings_lines(10)),
        icon_section(ICON_TIP, "明日", ["收盘后跑日决策，按目标组合再平衡并更新权益"]),
    ]
    return "\n\n".join(p for p in parts if p)


_SIDE_ICON = {"buy": "📤", "add": "📤", "sell": "📥", "reduce": "📥"}
_SIDE_LABEL = {"buy": "买入", "add": "加仓", "sell": "卖出", "reduce": "减仓", "hold": "持有"}
_REASON_LABEL = {
    "new_position": "新建",
    "exit_signal": "出场",
    "out_of_target": "掉出目标",
    "rebalance": "调仓",
    "within_buffer": "缓冲内",
}


def build_decision_push_body(payload: dict[str, Any]) -> str:
    """日决策推送正文：账户 + 明日作战池 + 卖出监控 + 持仓（晚间只定计划，不撮合）。"""
    paper = payload.get("paper") or {}
    acc = paper.get("account") or {}
    pool = payload.get("battle_pool") or []
    sell_watch = payload.get("sell_watch") or []
    from quant.narrative.exit_phrases import exit_reason_label

    pool_lines = [
        (
            f"{p.get('name')}({p.get('code')}) α={p.get('alpha')}"
            f"{p.get('alpha_note') or ''} #{p.get('rank')} {p.get('why') or ''}"
        ).rstrip()
        for p in pool[:8]
    ]
    sell_lines = []
    for s in sell_watch[:10]:
        flag = "⚡" if s.get("force_sell") else "·"
        hs = s.get("hard_stop")
        sell_lines.append(
            f"{flag} {s.get('name')}({s.get('code')}) 止损{hs} {exit_reason_label(s.get('reason'))}"
        )
    parts = [
        icon_section(
            ICON_ACCOUNT,
            "账户",
            [
                f"总资产 {money(acc.get('总资产'))}",
                f"现金 {money(acc.get('可用资金'))} | 市值 {money(acc.get('持仓市值'))}",
            ],
        ),
        icon_section(
            ICON_WATCH, f"明日作战池({payload.get('battle_pool_date', '')})", pool_lines
        ),
        icon_section(ICON_ORDER, "卖出监控", sell_lines),
        icon_section(
            ICON_HOLD,
            "持仓",
            [
                f"{h.get('code')} {h.get('name')} {h.get('shares')}股 @{h.get('cost')}"
                for h in (paper.get("holdings") or [])[:8]
            ],
        ),
    ]
    if payload.get("weights_source") == "registry_default":
        parts.insert(
            0,
            icon_section(
                ICON_TIP,
                "因子权重",
                ["registry 默认，未 walk-forward 校准；α 仅供参考"],
            ),
        )
    return "\n\n".join(p for p in parts if p)
