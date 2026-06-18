"""智能盯盘：按 payload 模板化推送（不经过 LLM）。"""

from __future__ import annotations

import re
from datetime import datetime

from quant.market.turnover import parse_turnover_yi, turnover_from_payload
from quant.narrative.ops_context import sample_no_trade_reasons
from quant.narrative.stock_lines import stock_name
from quant.pool.ths_rank_util import format_ths_rank_tags_brief, stock_ths_rank_tags
from quant.scoring.context import ScoreContext
from quant.scoring.tech_indicators import stock_daily_change_pct, to_float
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY, section_board_rows
from quant.signals.models import TradeSignal
from quant.store.state import (
    _holding_cost_price,
    _holding_quantity,
    compute_holdings_market_value,
    get_account,
    holding_mark_price,
    merge_payload_holdings,
    resolve_payload_holdings,
)
from quant.timeutil import parse_cn_datetime_str

_RED = "🔴"
_GREEN = "🟢"
_SECTION = "━━━━ {title} ━━━━"
_BOARD_BRIEF_N = 3
_WATCHLIST_ANOMALY_N = 8
_WEEKDAYS = "一二三四五六日"

_INDEX_LABELS = {
    "000001": ("上证", "上证"),
    "399001": ("深证", "深证"),
    "399006": ("创业板", "创"),
}


def _emoji_for_pct(pct: float | None) -> str:
    if pct is None:
        return _RED
    return _RED if pct >= 0 else _GREEN


def _fmt_pct(pct: float | None, *, short: bool = False) -> str:
    if pct is None:
        return "—"
    sign = "+" if pct >= 0 else ""
    if short:
        return f"{sign}{pct:.1f}%"
    return f"{sign}{pct:.2f}%"


def _fmt_price(px: float | None) -> str:
    if px is None or px <= 0:
        return "—"
    return f"{px:.2f}"


def _parse_header_dt(timestamp: str) -> datetime | None:
    return parse_cn_datetime_str(timestamp)


def _weekday_label(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return f"周{_WEEKDAYS[dt.weekday()]}"


def _index_row(payload: dict, code: str) -> dict | None:
    for row in payload.get("大盘指数") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("代码", "")).strip() == code:
            return row
    return None


def _index_change(payload: dict, code: str) -> float | None:
    row = _index_row(payload, code)
    if not row:
        return None
    return to_float(row.get("涨跌幅"))


def _turnover_compare_note(payload: dict) -> str:
    profit = payload.get("赚钱效应") or {}
    if not isinstance(profit, dict):
        return ""
    block = profit.get("成交额")
    today_yi: float | None = None
    if isinstance(block, dict):
        for key in ("今日累计", "今日全天"):
            today_yi = parse_turnover_yi(block.get(key))
            if today_yi is not None:
                break
        same_note = str(block.get("较昨日同时段") or "").strip()
        if same_note and today_yi is not None:
            m = re.search(r"([\d.]+)\s*亿", same_note)
            if m:
                delta = float(m.group(1))
                if "缩" in same_note:
                    delta = -delta
                base = today_yi - delta
                if base > 0:
                    pct = delta / base * 100
                    sign = "+" if pct >= 0 else ""
                    return f"（较昨{sign}{pct:.0f}%）"
            return f"（{same_note}）"
    if today_yi is None:
        today_yi = turnover_from_payload(payload)
    if today_yi is None:
        return ""
    yesterday_yi = parse_turnover_yi(profit.get("昨日成交额"))
    if yesterday_yi and yesterday_yi > 0:
        pct = (today_yi - yesterday_yi) / yesterday_yi * 100
        sign = "+" if pct >= 0 else ""
        return f"（较昨{sign}{pct:.0f}%）"
    note = str(profit.get("较昨日变动") or "").strip()
    if note:
        return f"（{note}）"
    return ""


def _turnover_yi(payload: dict) -> float | None:
    profit = payload.get("赚钱效应") or {}
    yi: float | None = None
    if isinstance(profit, dict):
        block = profit.get("成交额")
        if isinstance(block, dict):
            for key in ("今日累计", "今日全天"):
                yi = parse_turnover_yi(block.get(key))
                if yi is not None:
                    break
        if yi is None:
            yi = parse_turnover_yi(profit.get("今日成交额"))
    if yi is None:
        yi = turnover_from_payload(payload)
    return yi


def _format_turnover_compact(payload: dict) -> str:
    yi = _turnover_yi(payload)
    if yi is None:
        return "成交—"
    if yi >= 10000:
        wan_yi = yi / 10000.0
        body = f"{wan_yi:.2f}万亿".rstrip("0").rstrip(".")
        if not body.endswith("万亿"):
            body = f"{wan_yi:.2f}万亿"
    else:
        if abs(yi - int(yi)) < 0.05:
            body = f"{int(yi)}亿"
        else:
            body = f"{yi:.1f}亿".rstrip("0").rstrip(".")
            if not body.endswith("亿"):
                body += "亿"
    return f"成交{body}{_turnover_compare_note(payload)}"


def _format_index_compact_line(payload: dict) -> str:
    parts: list[str] = []
    for code, (_full, short) in _INDEX_LABELS.items():
        row = _index_row(payload, code)
        if not row:
            continue
        px = to_float(row.get("最新价"))
        chg = to_float(row.get("涨跌幅"))
        if px is None:
            continue
        px_s = f"{px:.0f}" if px >= 1000 else f"{px:.2f}"
        parts.append(f"{short}{px_s}{_emoji_for_pct(chg)}{_fmt_pct(chg, short=True)}")
    if not parts:
        return "暂无指数数据"
    parts.append(_format_turnover_compact(payload))
    return "  ".join(parts)


def _short_theme_label(name: str, *, max_len: int = 6) -> str:
    s = name.strip()
    if len(s) <= max_len:
        return s
    for sep in ("(", "（", "/"):
        if sep in s:
            s = s.split(sep, 1)[0]
            break
    return s[:max_len] if len(s) > max_len else s


def _market_tone_note(payload: dict) -> str:
    sh = _index_change(payload, "000001")
    sz = _index_change(payload, "399001")
    cyb = _index_change(payload, "399006")
    parts: list[str] = []
    if sh is not None and cyb is not None:
        if cyb >= 0.8 and sh <= 0:
            parts.append("创强沪弱")
        elif cyb >= 0.8 and sh is not None and sh < cyb - 0.8:
            parts.append("创强")
        elif sh is not None and sh >= 0.8 and cyb is not None and cyb < sh - 0.8:
            parts.append("沪强")
        elif sh is not None and sh <= -0.5 and cyb is not None and cyb <= -0.5:
            parts.append("普跌")
        elif sh is not None and sh >= 0.5 and cyb is not None and cyb >= 0.5:
            parts.append("普涨")
    if sz is not None and sh is not None and sz - sh >= 0.8:
        parts.append("深强沪弱")

    concept = payload.get(BOARD_CONCEPT) or {}
    if isinstance(concept, dict):
        inflow = section_board_rows(
            {BOARD_CONCEPT: concept}, BOARD_CONCEPT, "资金流入榜", limit=2
        )
        themes = [_short_theme_label(_theme_name(r)) for r in inflow if _theme_name(r)]
        if themes:
            parts.append("/".join(themes) + "强")
    return " · ".join(parts)


def _format_title_line(payload: dict, timestamp: str) -> str:
    dt = _parse_header_dt(timestamp)
    hm = dt.strftime("%H:%M") if dt else "—"
    wd = _weekday_label(dt)
    tone = _market_tone_note(payload)
    if wd and tone:
        return f"📡 盘中 {hm}（{wd}）· {tone}"
    if wd:
        return f"📡 盘中 {hm}（{wd}）"
    if tone:
        return f"📡 盘中 {hm} · {tone}"
    return f"📡 盘中 {hm}"


def _theme_name(row: dict) -> str:
    return str(row.get("行业") or row.get("板块") or "").strip()


def _theme_chg_pct(row: dict) -> float | None:
    return to_float(row.get("行业-涨跌幅") if row.get("行业-涨跌幅") is not None else row.get("涨跌幅"))


def _net_yi(row: dict) -> float | None:
    for key in ("净额", "净流入"):
        v = to_float(row.get(key))
        if v is not None:
            return v
    return None


def _fmt_yi_compact(v: float | None) -> str:
    if v is None:
        return ""
    sign = "+" if v >= 0 else "-"
    av = abs(v)
    if av >= 10 and abs(av - round(av)) < 0.05:
        body = f"{av:.0f}"
    else:
        body = f"{av:.1f}".rstrip("0").rstrip(".")
    return f"{sign}{body}亿"


def _fmt_fund_brief_items(rows: list[dict], *, outflow: bool = False) -> list[str]:
    parts: list[str] = []
    for i, row in enumerate(rows[:_BOARD_BRIEF_N]):
        if not isinstance(row, dict):
            continue
        name = _theme_name(row)
        if not name:
            continue
        net = _net_yi(row)
        if outflow and net is not None and net > 0:
            net = -net
        fund = _fmt_yi_compact(net)
        chg = _theme_chg_pct(row)
        if i == 0 and chg is not None:
            parts.append(f"{name}{fund}({_fmt_pct(chg, short=True)})")
        else:
            parts.append(f"{name}{fund}")
    return parts


def _fmt_gain_brief_items(rows: list[dict]) -> list[str]:
    parts: list[str] = []
    for row in rows[:_BOARD_BRIEF_N]:
        if not isinstance(row, dict):
            continue
        name = _theme_name(row)
        chg = _theme_chg_pct(row)
        if not name or chg is None:
            continue
        parts.append(f"{name}{_fmt_pct(chg, short=True)}")
    return parts


def _format_board_brief_section(
    lines: list[str],
    *,
    title: str,
    block: dict,
    section: str,
) -> None:
    if not isinstance(block, dict):
        return
    payload_slice = {section: block}
    inflow = section_board_rows(payload_slice, section, "资金流入榜", limit=_BOARD_BRIEF_N)
    outflow = section_board_rows(payload_slice, section, "资金流出榜", limit=_BOARD_BRIEF_N)
    gain = section_board_rows(payload_slice, section, "涨幅榜", limit=_BOARD_BRIEF_N)
    loss = section_board_rows(payload_slice, section, "跌幅榜", limit=_BOARD_BRIEF_N)

    body: list[str] = []
    in_parts = _fmt_fund_brief_items(inflow)
    out_parts = _fmt_fund_brief_items(outflow, outflow=True)
    gain_parts = _fmt_gain_brief_items(gain)
    loss_parts = _fmt_gain_brief_items(loss)
    if in_parts:
        body.append("流入：" + "  ".join(in_parts))
    if out_parts:
        body.append("流出：" + "  ".join(out_parts))
    if gain_parts:
        body.append("涨幅：" + "  ".join(gain_parts))
    if loss_parts:
        body.append("跌幅：" + "  ".join(loss_parts))
    if not body:
        return
    lines.append(_SECTION.format(title=title))
    lines.extend(body)
    lines.append("")


def _stock_flow_yi(stock: dict) -> float | None:
    flow = stock.get("个股资金流") or {}
    if not isinstance(flow, dict):
        return None
    raw = flow.get("净额")
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    wan = float(m.group())
    return wan / 10000.0


def _flow_brief(yi: float | None) -> str:
    if yi is None:
        return "资金—"
    if yi >= 0:
        return f"流入{_fmt_yi_compact(yi)}"
    return f"流出{_fmt_yi_compact(yi)}"


def _watchlist_tag_note(stock: dict) -> str:
    tags = format_ths_rank_tags_brief(stock_ths_rank_tags(stock))
    if not tags:
        return ""
    if tags[0] == "持续放量":
        return "放量"
    return tags[0]


def _watchlist_anomaly_score(row: dict) -> float:
    chg = stock_daily_change_pct(row) or 0.0
    flow = abs(_stock_flow_yi(row) or 0.0)
    tag_bonus = 3.0 if _watchlist_tag_note(row) else 0.0
    return abs(chg) * 2.0 + flow + tag_bonus


def _format_watchlist_anomaly_lines(payload: dict) -> tuple[str, list[str]]:
    rows = [r for r in (payload.get("自选股") or []) if isinstance(r, dict)]
    total = len(rows)
    if not rows:
        return _SECTION.format(title="自选异动"), ["暂无自选股"]
    ranked = sorted(rows, key=_watchlist_anomaly_score, reverse=True)
    picked = ranked[:_WATCHLIST_ANOMALY_N]
    title = _SECTION.format(title=f"自选异动（{len(picked)}/{total}）")
    lines: list[str] = []
    for row in picked:
        name = stock_name(row)
        if not name:
            continue
        chg = stock_daily_change_pct(row)
        emoji = _emoji_for_pct(chg)
        tag = _watchlist_tag_note(row)
        tag_part = f"  {tag}" if tag else ""
        lines.append(f"{emoji} {name}  {_fmt_pct(chg)}  {_flow_brief(_stock_flow_yi(row))}{tag_part}")
    return title, lines if lines else ["暂无自选股"]


def _holding_float_pnl(h: dict) -> tuple[float | None, float | None]:
    cost = _holding_cost_price(h)
    px = holding_mark_price(h)
    qty = _holding_quantity(h)
    if cost is None or px is None or cost <= 0 or qty <= 0:
        return None, None
    pct = (px - cost) / cost * 100
    amount = (px - cost) * qty
    return pct, amount


def _portfolio_total_assets(holdings: list[dict]) -> float:
    account = get_account()
    total = float(account.get("总资产") or 0)
    if total > 0:
        return total
    cash = float(account.get("可用资金") or 0)
    mv = compute_holdings_market_value(holdings)
    return cash + mv if cash + mv > 0 else 0.0


def _holding_position_pct(h: dict, *, total_assets: float) -> float | None:
    qty = _holding_quantity(h)
    px = holding_mark_price(h)
    if qty <= 0 or px is None or px <= 0 or total_assets <= 0:
        return None
    return qty * px / total_assets * 100


def _holdings_summary(holdings: list[dict], *, total_assets: float) -> str:
    if not holdings:
        return "暂无持仓"
    total_pos = 0.0
    total_pnl = 0.0
    pnl_known = False
    for h in holdings:
        pos = _holding_position_pct(h, total_assets=total_assets)
        if pos is not None:
            total_pos += pos
        _, amount = _holding_float_pnl(h)
        if amount is not None:
            total_pnl += amount
            pnl_known = True
    pnl_s = f"浮盈{total_pnl:+.0f}元" if pnl_known else "浮盈—"
    if total_pos > 0:
        return f"持仓（{len(holdings)}只 · 总仓{total_pos:.0f}% · {pnl_s}）"
    return f"持仓（{len(holdings)}只 · {pnl_s}）"


def _format_holding_lines(payload: dict) -> tuple[str, list[str]]:
    holdings = resolve_payload_holdings(payload)
    if not holdings:
        return _SECTION.format(title="持仓"), ["暂无持仓"]
    total_assets = _portfolio_total_assets(holdings)
    header = _SECTION.format(title=_holdings_summary(holdings, total_assets=total_assets))
    lines: list[str] = []
    for h in holdings:
        name = stock_name(h) or str(h.get("股票代码", "")).strip()
        if not name:
            continue
        cost = _holding_cost_price(h)
        px = holding_mark_price(h)
        qty = _holding_quantity(h)
        pos_pct = _holding_position_pct(h, total_assets=total_assets)
        pct, amount = _holding_float_pnl(h)
        daily = stock_daily_change_pct(h)
        emoji = _emoji_for_pct(pct if pct is not None else daily)
        pct_s = _fmt_pct(pct if pct is not None else daily)
        if amount is not None and amount >= 0:
            pnl_note = f"  浮盈+{abs(amount):.0f}元"
        elif amount is not None:
            pnl_note = f"  浮亏-{abs(amount):.0f}元"
        else:
            pnl_note = ""
        qty_part = f"  {qty}股" if qty > 0 else ""
        pos_part = f"  仓{pos_pct:.1f}%" if pos_pct is not None else ""
        cost_px = f"成本{_fmt_price(cost)}→{_fmt_price(px)}"
        lines.append(f"{emoji} {name}{qty_part}{pos_part}  {cost_px}  {pct_s}{pnl_note}")
    return header, lines if lines else ["暂无持仓"]


def _signal_trigger_text(sig: TradeSignal) -> str:
    kind = str(sig.signal_kind or sig.sell_type or "").strip()
    reason = str(sig.reason or "").strip()
    if kind:
        return kind
    if reason.startswith("[") and "]" in reason:
        return reason.split("]", 1)[0].lstrip("[")
    if "；" in reason:
        return reason.split("；", 1)[0][:24]
    return reason[:24] if reason else "策略触发"


def _format_signal_lines(
    raw_buy: list[TradeSignal],
    raw_sell: list[TradeSignal],
    *,
    ctx: ScoreContext | None = None,
    mode: str = "during_market",
) -> list[str]:
    lines: list[str] = ["🚨 买卖信号"]
    if raw_buy or raw_sell:
        for sig in raw_buy:
            trigger = _signal_trigger_text(sig)
            px = _fmt_price(sig.price if sig.price > 0 else None)
            lines.append(f"{_RED} 买入：{sig.name}    触发「{trigger}」现价{px}")
        for sig in raw_sell:
            trigger = _signal_trigger_text(sig)
            px = _fmt_price(sig.price if sig.price > 0 else None)
            lines.append(f"{_GREEN} 卖出：{sig.name}    触发「{trigger}」现价{px}")
        return lines

    if ctx is not None:
        buy_notes, sell_notes = sample_no_trade_reasons(
            ctx,
            mode=mode,
            raw_buy=raw_buy,
            raw_sell=raw_sell,
            per_side=2,
        )
        if buy_notes or sell_notes:
            summary_parts: list[str] = []
            if sell_notes:
                summary_parts.append(f"{len(sell_notes)}只持仓暂不减")
            if buy_notes:
                summary_parts.append(f"{len(buy_notes)}只自选强度不够")
            lines.append("暂无信号 · " + " · ".join(summary_parts))
            for note in sell_notes:
                lines.append(f"·未卖 {note}")
            for note in buy_notes:
                lines.append(f"·未买 {note}")
            return lines

    lines.append("暂无买卖信号")
    return lines


def build_during_market_push(
    payload: dict,
    *,
    timestamp: str,
    raw_buy: list[TradeSignal] | None = None,
    raw_sell: list[TradeSignal] | None = None,
    ctx: ScoreContext | None = None,
    mode: str = "during_market",
) -> str:
    """生成智能盯盘完整推送正文（含标题行）。"""
    payload = merge_payload_holdings(dict(payload))
    lines: list[str] = [
        _format_title_line(payload, timestamp),
        _format_index_compact_line(payload),
        "",
    ]

    hold_title, hold_lines = _format_holding_lines(payload)
    lines.append(hold_title)
    lines.extend(hold_lines)
    lines.append("")

    lines.extend(
        _format_signal_lines(
            raw_buy or [],
            raw_sell or [],
            ctx=ctx,
            mode=mode,
        )
    )
    lines.append("")

    wl_title, wl_lines = _format_watchlist_anomaly_lines(payload)
    lines.append(wl_title)
    lines.extend(wl_lines)
    lines.append("")

    industry = payload.get(BOARD_INDUSTRY) or {}
    concept = payload.get(BOARD_CONCEPT) or {}
    _format_board_brief_section(
        lines,
        title="行业（流入/流出/涨跌）",
        block=industry if isinstance(industry, dict) else {},
        section=BOARD_INDUSTRY,
    )
    _format_board_brief_section(
        lines,
        title="概念（流入/流出/涨跌）",
        block=concept if isinstance(concept, dict) else {},
        section=BOARD_CONCEPT,
    )

    return "\n".join(lines).rstrip()
