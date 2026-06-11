"""规则引擎结论 → 供 LLM 叙述引用的结构化摘要。"""

from __future__ import annotations

from typing import Any

from quant.config import load_gates_config
from quant.gates.rules import check_global_gates, format_position_control
from quant.narrative.holdings_context import format_holdings_performance, format_holdings_summary
from quant.narrative.history_context import (
    format_concept_rotation,
    format_today_pnl_summary,
    format_today_trades,
    format_yesterday_trades,
)
from quant.narrative.stock_lines import (
    format_optional_performance_lines,
    format_score_bullet,
    name_code_label,
    stock_code,
    stock_name,
)
from quant.scoring.context import (
    ScoreContext,
    index_change,
    profit_effect,
    zt_height,
)
from quant.narrative.push_style import BRIEF_PREAMBLE, profit_effect_level
from quant.scoring.global_macro import global_macro_for_scoring
from quant.scoring.theme_tracker import theme_detail
from quant.scoring.tech_indicators import stock_daily_change_pct
from quant.strategy.main_wave import is_in_main_wave


def _collect_main_wave_stocks(ctx: ScoreContext, payload: dict) -> list[str]:
    mw_cfg = load_gates_config().get("main_wave") or {}
    seen: set[str] = set()
    out: list[str] = []
    for key in ("同花顺人气榜", "自选股", "持仓股", "盘口异动"):
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = stock_code(row)
            if not code or code in seen:
                continue
            ok, note = is_in_main_wave(row, mw_cfg)
            if not ok:
                continue
            seen.add(code)
            chg = stock_daily_change_pct(row)
            extra = f" 涨跌幅{chg:+.2f}%" if chg is not None else ""
            tag = f" [{note}]" if note else ""
            out.append(f"{stock_name(row) or code}({code}){extra}{tag}")
    return out


def _format_watchlist_scores(scores: list[Any] | None, *, limit: int = 8) -> list[str]:
    if not scores:
        return []
    lines: list[str] = []
    for s in sorted(scores, key=lambda x: getattr(x, "total", 0), reverse=True)[:limit]:
        mark = "达标" if getattr(s, "passed_threshold", False) else "未达标"
        lines.append(
            f"· {getattr(s, 'name', '')}({getattr(s, 'code', '')}) "
            f"{getattr(s, 'total', 0):.1f}分 [{mark}]"
        )
    return lines


def _append_holdings_performance_block(lines: list[str]) -> None:
    lines.append("")
    lines.append("持仓股表现（第三节须与此一致）：")


def build_engine_brief(
    ctx: ScoreContext,
    payload: dict,
    *,
    mode: str = "",
    watchlist_scores: list[Any] | None = None,
    watchlist_pool: list[dict] | None = None,
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

    lines.append("")
    lines.append("当前持仓（正文须与此一致；有持仓禁止写「空仓」「无持仓」）：")
    lines.append(format_holdings_summary(payload))

    rotation = format_concept_rotation()
    if rotation and "暂无" not in rotation[:20]:
        lines.append("")
        lines.append("近几日概念轮动：")
        lines.append(rotation)

    if mode == "during_market":
        macro = global_macro_for_scoring()
        if macro:
            label = {"bearish": "利空", "neutral": "中性", "bullish": "利好"}.get(
                macro["sentiment"], macro["sentiment"]
            )
            reason = macro.get("reason") or "—"
            lines.append(
                f"全球宏观评分（规则引擎，勿与新闻摘要重复展开）：{label} "
                f"{macro['score']:.0f}分（{reason}）"
            )

    if mode == "pre_market":
        trades = format_yesterday_trades()
        if trades:
            lines.append("")
            lines.append(f"上一交易日成交：{trades}")
        lines.append("持续确认：盘前不计入；盘中自09:37起条件须连续保持足够时长与轮次。")

    if mode == "post_market_lunch":
        opt_rows = [r for r in (payload.get("自选股") or []) if isinstance(r, dict)]
        lines.append("")
        lines.append("自选股表现范围：")
        lines.extend(format_optional_performance_lines(opt_rows) or ["· 暂无"])
        _append_holdings_performance_block(lines)
        lines.append(format_holdings_performance(payload))

    if mode == "post_market_evening":
        trades = format_today_trades()
        lines.append("")
        lines.append("今日操作（成交记录，操作复盘须与此一致）：")
        lines.append(trades)

        pnl = format_today_pnl_summary(payload)
        lines.append("")
        lines.append("当日盈亏参考（盈亏总结须与此一致，勿夸大）：")
        lines.append(pnl)

        if watchlist_scores is not None:
            score_lines = _format_watchlist_scores(watchlist_scores)
            if score_lines:
                lines.append("")
                lines.append("晚间候选池评分：")
                lines.extend(score_lines)

        if watchlist_pool is not None:
            added_codes = {
                str(r.get("股票代码", "")).strip()
                for r in (watchlist_added or [])
            }
            pre_existing = [
                r
                for r in watchlist_pool
                if str(r.get("股票代码", "")).strip() not in added_codes
            ]
            lines.append("")
            lines.append("自选股表现范围（不含本轮新入选）：")
            if pre_existing:
                for r in pre_existing:
                    lines.append(format_score_bullet(r))
            else:
                lines.append("· 暂无")
            if watchlist_added:
                names = "、".join(name_code_label(r) for r in watchlist_added)
                lines.append(f"本轮新入选勿写入「自选股表现」：{names}")

        _append_holdings_performance_block(lines)
        lines.append(format_holdings_performance(payload))

    return "\n".join(lines)
