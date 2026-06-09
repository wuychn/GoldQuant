"""规则引擎结论 → 供 LLM 叙述引用的结构化摘要。"""

from __future__ import annotations

from typing import Any

from quant.config import load_gates_config
from quant.gates.rules import check_global_gates, format_position_control
from quant.narrative.history_context import format_concept_rotation, format_yesterday_trades
from quant.scoring.context import (
    ScoreContext,
    index_change,
    profit_effect,
    zt_height,
)
from quant.narrative.push_style import BRIEF_PREAMBLE, profit_effect_level
from quant.scoring.theme_tracker import theme_detail
from quant.scoring.tech_indicators import stock_daily_change_pct
from quant.store.watchlist import watchlist_retain_days
from quant.strategy.main_wave import is_in_main_wave


def _stock_code(row: dict) -> str:
    return str(row.get("股票代码") or row.get("代码") or "").strip()


def _stock_name(row: dict) -> str:
    return str(row.get("股票名称") or row.get("名称") or "").strip()


def _collect_main_wave_stocks(ctx: ScoreContext, payload: dict) -> list[str]:
    mw_cfg = load_gates_config().get("main_wave") or {}
    seen: set[str] = set()
    out: list[str] = []
    for key in ("同花顺人气榜", "自选股", "持仓股", "盘口异动"):
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = _stock_code(row)
            if not code or code in seen:
                continue
            ok, note = is_in_main_wave(row, mw_cfg)
            if not ok:
                continue
            seen.add(code)
            chg = stock_daily_change_pct(row)
            extra = f" 涨跌幅{chg:+.2f}%" if chg is not None else ""
            tag = f" [{note}]" if note else ""
            out.append(f"{_stock_name(row) or code}({code}){extra}{tag}")
    return out


def _format_watchlist_scores(scores: list[Any] | None, *, limit: int = 8) -> list[str]:
    if not scores:
        return []
    lines: list[str] = []
    for s in sorted(scores, key=lambda x: getattr(x, "total", 0), reverse=True)[:limit]:
        mark = "达标" if getattr(s, "passed_threshold", False) else "未达标"
        lines.append(f"· {getattr(s, 'name', '')}({getattr(s, 'code', '')}) {getattr(s, 'total', 0):.1f}分 [{mark}]")
    return lines


def build_engine_brief(
    ctx: ScoreContext,
    payload: dict,
    *,
    mode: str = "",
    watchlist_scores: list[Any] | None = None,
    watchlist_added: list[dict] | None = None,
) -> str:
    """组装写作参考块（供 LLM 引用，勿原样复制标签进飞书正文）。"""
    detail = theme_detail(payload)
    gain = detail.get("当日涨幅概念") or []
    fund = detail.get("当日资金概念") or []
    accel = _collect_main_wave_stocks(ctx, payload)

    profit = profit_effect(payload)
    up = int(profit.get("上涨", 0) or 0)
    down = int(profit.get("下跌", 0) or 0)
    zt_cnt = int(profit.get("涨停", 0) or 0)
    dt_cnt = int(profit.get("跌停", 0) or 0)
    idx = index_change(payload)
    height = zt_height(payload)
    idx_s = f"{idx:.2f}" if idx is not None else "—"
    effect_level = profit_effect_level(payload)
    gates = check_global_gates(ctx)

    lines = [
        BRIEF_PREAMBLE,
        f"赚钱效应：{effect_level}（上证{idx_s}% 上涨{up}/下跌{down} 涨停{zt_cnt}/跌停{dt_cnt} 最高{height}板）",
        f"当日涨幅概念：{'、'.join(gain[:10]) if gain else '暂无'}",
        f"资金流入概念：{'、'.join(fund[:10]) if fund else '暂无'}",
        f"主升波段（{len(accel)}只）：{'、'.join(accel[:12]) if accel else '暂无'}",
    ]
    if gates.passed:
        lines.append(f"仓位控制：{format_position_control(payload)}")
    else:
        lines.append(gates.push_summary(payload))

    rotation = format_concept_rotation()
    if rotation and "暂无" not in rotation[:20]:
        lines.append("")
        lines.append("近几日概念轮动：")
        lines.append(rotation)

    if mode == "pre_market":
        trades = format_yesterday_trades()
        if trades:
            lines.append("")
            lines.append(f"上一交易日成交：{trades}")
        lines.append("三确认：盘前不计数，买卖确认自09:37盘中首次调度起算。")

    if mode == "post_market_evening" and watchlist_scores is not None:
        score_lines = _format_watchlist_scores(watchlist_scores)
        if score_lines:
            lines.append("")
            lines.append("晚间候选池评分：")
            lines.extend(score_lines)
        if watchlist_added is not None:
            retain = watchlist_retain_days()
            if watchlist_added:
                names = "、".join(f"{r.get('股票名称')}({r.get('股票代码')})" for r in watchlist_added)
                lines.append(f"本轮自选池（滚动保留{retain}交易日）：{names}")
            else:
                lines.append(f"本轮自选池为空（滚动保留{retain}交易日）")

    return "\n".join(lines)
