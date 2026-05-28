"""规则引擎结论 → 供 LLM 叙述引用的结构化摘要（LLM 不得自行推断主线/龙头）。"""

from __future__ import annotations

from typing import Any

from quant.config import load_gates_config
from quant.gates.rules import check_global_gates
from quant.narrative.history_context import format_concept_rotation, format_yesterday_trades
from quant.scoring.context import (
    ScoreContext,
    index_change,
    infer_regime,
    profit_effect,
    zt_height,
)
from quant.scoring.theme_tracker import theme_detail
from quant.strategy.main_wave import is_theme_leader


def _stock_code(row: dict) -> str:
    return str(row.get("股票代码") or row.get("代码") or "").strip()


def _stock_name(row: dict) -> str:
    return str(row.get("股票名称") or row.get("名称") or "").strip()


def _collect_theme_leaders(ctx: ScoreContext, payload: dict) -> list[str]:
    mw_cfg = load_gates_config().get("main_wave") or {}
    max_rank = int(mw_cfg.get("leader_max_rank", 15))
    seen: set[str] = set()
    out: list[str] = []
    for key in ("同花顺人气榜", "自选股", "持仓股"):
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = _stock_code(row)
            if not code or code in seen:
                continue
            if not is_theme_leader(row, ctx, max_rank=max_rank):
                continue
            seen.add(code)
            rank = row.get("人气排名")
            rank_s = f" 人气{rank}" if rank is not None else ""
            out.append(f"{_stock_name(row) or code}({code}){rank_s}")
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
    """组装「程序结论」块；LLM 叙述必须与此一致，不得自行推断。"""
    detail = theme_detail(payload)
    confirmed = detail.get("确认主线") or []
    gain = detail.get("当日涨幅概念") or []
    fund = detail.get("当日资金概念") or []
    leaders = _collect_theme_leaders(ctx, payload)

    profit = profit_effect(payload)
    up = int(profit.get("上涨", 0) or 0)
    down = int(profit.get("下跌", 0) or 0)
    zt_cnt = int(profit.get("涨停", 0) or 0)
    dt_cnt = int(profit.get("跌停", 0) or 0)
    idx = index_change(payload)
    height = zt_height(payload)
    regime = infer_regime(payload)
    idx_s = f"{idx:.2f}" if idx is not None else "—"

    lines = [
        "【程序结论 · 规则引擎输出，叙述须与此一致，禁止自行推断或改写】",
        f"仓位档位：{regime}（上证{idx_s}% 上涨{up}/下跌{down} 涨停{zt_cnt}/跌停{dt_cnt} 最高{height}板）",
        f"确认主线（{len(confirmed)}）：{'、'.join(confirmed) if confirmed else '暂无'}",
        f"当日涨幅榜概念：{'、'.join(gain[:10]) if gain else '暂无'}",
        f"当日资金流入概念：{'、'.join(fund[:10]) if fund else '暂无'}",
        f"程序认定主线龙头（{len(leaders)}）：{'、'.join(leaders) if leaders else '暂无（或当前 payload 无人气/自选数据）'}",
        f"全局门禁：{check_global_gates(ctx).summary()}",
    ]

    rotation = format_concept_rotation()
    if rotation and "暂无" not in rotation[:20]:
        lines.append("")
        lines.append("【程序归档 · 近几日概念轮动】")
        lines.append(rotation)

    if mode == "pre_market":
        trades = format_yesterday_trades()
        if trades:
            lines.append("")
            lines.append(f"【程序归档 · 上一交易日成交】{trades}")

    if mode == "post_market_evening" and watchlist_scores is not None:
        score_lines = _format_watchlist_scores(watchlist_scores)
        if score_lines:
            lines.append("")
            lines.append("【程序评分 · 晚间候选池】")
            lines.extend(score_lines)
        if watchlist_added:
            names = "、".join(f"{r.get('股票名称')}({r.get('股票代码')})" for r in watchlist_added)
            lines.append(f"【程序决策 · 新增自选】{names}")
        elif watchlist_scores is not None:
            lines.append("【程序决策 · 新增自选】本轮无新增")

    return "\n".join(lines)
