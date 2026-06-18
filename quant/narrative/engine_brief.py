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
from quant.timeutil import intraday_session_time_line
from quant.scoring.global_macro import global_macro_for_scoring
from quant.scoring.theme_boards import board_gain_fund_lists
from quant.scoring.tech_indicators import stock_daily_change_pct
from quant.strategy.main_wave import is_in_main_wave


def _collect_main_wave_stocks(ctx: ScoreContext, payload: dict) -> list[str]:
    mw_cfg = load_gates_config().get("main_wave") or {}
    seen: set[str] = set()
    out: list[str] = []
    for key in ("同花顺人气榜", "自选股", "持仓股", "创新高", "持续上涨", "持续放量", "量价齐升"):
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


def _append_pnl_account_block(lines: list[str], payload: dict, *, mode: str) -> None:
    pnl = format_today_pnl_summary(payload)
    lines.append("")
    if mode == "during_market":
        label = "当日盈亏与账户（须在正文写出，严格依据下列数据）："
    elif mode == "post_market_lunch":
        label = "当日盈亏与账户（须在正文写出，严格依据下列数据）："
    else:
        label = "当日盈亏与账户（「总结与展望」须与此一致，勿夸大）："
    lines.append(label)
    lines.append(pnl)


def _append_holdings_performance_block(lines: list[str]) -> None:
    lines.append("")
    lines.append("持仓股表现（第三节须与此一致）：")


def _append_theme_board_brief(lines: list[str], payload: dict, *, mode: str) -> None:
    """概念/行业榜单摘要：盘前或无板块数据时不写；无数据时不写「暂无」占位。"""
    if mode == "pre_market":
        return
    if not payload.get("概念板块") and not payload.get("行业板块"):
        return
    concept_gain, concept_fund = board_gain_fund_lists(payload, "概念板块")
    industry_gain, industry_fund = board_gain_fund_lists(payload, "行业板块")
    if concept_gain:
        lines.append(f"当日涨幅概念：{'、'.join(concept_gain[:10])}")
    if concept_fund:
        lines.append(f"资金流入概念：{'、'.join(concept_fund[:10])}")
    if industry_gain:
        lines.append(f"当日涨幅行业：{'、'.join(industry_gain[:10])}")
    if industry_fund:
        lines.append(f"资金流入行业：{'、'.join(industry_fund[:10])}")


def _append_main_wave_brief(lines: list[str], accel: list[str]) -> None:
    if not accel:
        return
    lines.append(f"主升波段（{len(accel)}只）：{'、'.join(accel[:12])}")


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
    ]
    _append_theme_board_brief(lines, payload, mode=mode)
    _append_main_wave_brief(lines, accel)
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
        lines.append(intraday_session_time_line())
        _append_pnl_account_block(lines, payload, mode=mode)
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
        lines.append("持续确认：盘前不计入；买入当日有效、约10分钟+2轮（连续竞价计时），成交前再验。")

    if mode == "post_market_lunch":
        _append_pnl_account_block(lines, payload, mode=mode)
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
        _append_pnl_account_block(lines, payload, mode=mode)

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
