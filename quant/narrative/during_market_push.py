"""智能盯盘：按 payload 模板化推送（不经过 LLM）。"""

from __future__ import annotations

import re
from typing import Any

from quant.market.turnover import parse_turnover_yi, turnover_from_payload
from quant.narrative.stock_lines import stock_name
from quant.pool.ths_rank_util import format_ths_rank_tags_brief, stock_ths_rank_tags
from quant.scoring.tech_indicators import stock_daily_change_pct, to_float
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY, section_board_rows
from quant.signals.models import TradeSignal
from quant.store.state import (
    _holding_cost_price,
    _holding_quantity,
    holding_mark_price,
    merge_payload_holdings,
    resolve_payload_holdings,
)
from quant.timeutil import parse_cn_datetime_str

_RED = "🔴"
_GREEN = "🟢"
_SECTION = "━━━━ {title} ━━━━"

_INDEX_LABELS = {
    "000001": "上证",
    "399001": "深证",
    "399006": "创业板",
}


def _emoji_for_pct(pct: float | None) -> str:
    if pct is None:
        return _RED
    return _RED if pct >= 0 else _GREEN


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def _fmt_price(px: float | None) -> str:
    if px is None or px <= 0:
        return "—"
    if px >= 1000:
        return f"{px:.2f}"
    if px >= 100:
        return f"{px:.2f}"
    return f"{px:.2f}"


def _parse_header_time(timestamp: str) -> tuple[str, str]:
    dt = parse_cn_datetime_str(timestamp)
    if dt:
        return dt.strftime("%H:%M"), dt.strftime("%Y-%m-%d")
    parts = timestamp.strip().split()
    if len(parts) >= 2:
        return parts[1][:5], parts[0]
    return "—", timestamp[:10] if timestamp else "—"


def _index_row(payload: dict, code: str) -> dict | None:
    for row in payload.get("大盘指数") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("代码", "")).strip() == code:
            return row
    return None


def _format_index_lines(payload: dict) -> list[str]:
    lines: list[str] = []
    for code, label in _INDEX_LABELS.items():
        row = _index_row(payload, code)
        if not row:
            continue
        px = to_float(row.get("最新价"))
        chg = to_float(row.get("涨跌幅"))
        emoji = _emoji_for_pct(chg)
        px_s = _fmt_price(px)
        chg_s = _fmt_pct(chg)
        lines.append(f"{label} {px_s}  {emoji} {chg_s}")
    return lines


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
                    return f"（较昨日{sign}{pct:.0f}%）"
            return f"（{same_note}）"
    if today_yi is None:
        today_yi = turnover_from_payload(payload)
    if today_yi is None:
        return ""
    yesterday_yi = parse_turnover_yi(profit.get("昨日成交额"))
    if yesterday_yi and yesterday_yi > 0:
        pct = (today_yi - yesterday_yi) / yesterday_yi * 100
        sign = "+" if pct >= 0 else ""
        return f"（较昨日{sign}{pct:.0f}%）"
    note = str(profit.get("较昨日变动") or "").strip()
    if note:
        return f"（{note}）"
    return ""


def _format_turnover_line(payload: dict) -> str:
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
    if yi is None:
        return "成交 —"
    yi_s = str(int(yi)) if abs(yi - int(yi)) < 0.05 else f"{yi:.1f}"
    return f"成交 {yi_s}亿{_turnover_compare_note(payload)}"


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


def _fmt_yi_signed(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    av = abs(v)
    body = str(int(av)) if abs(av - int(av)) < 0.05 else f"{av:.1f}"
    return f"{sign}{body}亿"


def _pad_name(name: str, width: int = 8) -> str:
    n = len(name)
    if n >= width:
        return name
    return name + " " * (width - n)


def _format_board_fund_lines(
    rows: list[dict],
    *,
    limit: int = 10,
    outflow: bool = False,
) -> list[str]:
    lines: list[str] = []
    for i, row in enumerate(rows[:limit], 1):
        if not isinstance(row, dict):
            continue
        name = _theme_name(row)
        if not name:
            continue
        net = _net_yi(row)
        if outflow and net is not None and net > 0:
            net = -net
        chg = _theme_chg_pct(row)
        emoji = _emoji_for_pct(net if net is not None else chg)
        lines.append(
            f"{emoji} {i}. {_pad_name(name)}  {_fmt_yi_signed(net)}  {_fmt_pct(chg)}"
        )
    return lines


def _format_board_gain_lines(rows: list[dict], *, limit: int = 10) -> list[str]:
    lines: list[str] = []
    for i, row in enumerate(rows[:limit], 1):
        if not isinstance(row, dict):
            continue
        name = _theme_name(row)
        if not name:
            continue
        chg = _theme_chg_pct(row)
        emoji = _emoji_for_pct(chg)
        lines.append(f"{emoji} {i}. {_pad_name(name, 10)}  {_fmt_pct(chg)}")
    return lines


def _format_theme_board_section(
    lines: list[str],
    *,
    title: str,
    rows: list[dict],
    kind: str,
) -> None:
    lines.append(_SECTION.format(title=title))
    if kind == "fund":
        body = _format_board_fund_lines(rows, outflow="流出" in title)
    else:
        body = _format_board_gain_lines(rows)
    if body:
        lines.extend(body)
    else:
        lines.append("暂无数据")
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


def _flow_phrase(yi: float | None) -> str:
    if yi is None:
        return "资金 —"
    if yi >= 0:
        return f"资金流入 +{abs(yi):.1f} 亿"
    return f"资金流出 -{abs(yi):.1f} 亿"


def _watchlist_tag_note(stock: dict) -> str:
    tags = format_ths_rank_tags_brief(stock_ths_rank_tags(stock))
    if not tags:
        return ""
    mapped: list[str] = []
    for t in tags:
        if t == "持续放量":
            mapped.append("放量")
        else:
            mapped.append(t)
    return mapped[0]


def _format_watchlist_lines(payload: dict) -> list[str]:
    lines: list[str] = []
    for row in payload.get("自选股") or []:
        if not isinstance(row, dict):
            continue
        name = stock_name(row)
        if not name:
            continue
        chg = stock_daily_change_pct(row)
        emoji = _emoji_for_pct(chg)
        chg_s = _fmt_pct(chg)
        flow = _flow_phrase(_stock_flow_yi(row))
        tag = _watchlist_tag_note(row)
        tail = f"  {tag}" if tag else ""
        lines.append(f"{emoji} {name}   {chg_s}  {flow}{tail}")
    return lines


def _holding_float_pnl(h: dict) -> tuple[float | None, float | None]:
    cost = _holding_cost_price(h)
    px = holding_mark_price(h)
    qty = _holding_quantity(h)
    if cost is None or px is None or cost <= 0 or qty <= 0:
        return None, None
    pct = (px - cost) / cost * 100
    amount = (px - cost) * qty
    return pct, amount


def _format_holding_lines(payload: dict) -> list[str]:
    lines: list[str] = []
    for h in resolve_payload_holdings(payload):
        name = stock_name(h) or str(h.get("股票代码", "")).strip()
        if not name:
            continue
        cost = _holding_cost_price(h)
        px = holding_mark_price(h)
        pct, amount = _holding_float_pnl(h)
        daily = stock_daily_change_pct(h)
        emoji = _emoji_for_pct(pct if pct is not None else daily)
        cost_s = _fmt_price(cost)
        px_s = _fmt_price(px)
        pct_s = _fmt_pct(pct if pct is not None else daily)
        if amount is not None and amount >= 0:
            pnl_note = f"  浮盈+{abs(amount):.0f}元"
        elif amount is not None:
            pnl_note = f"  浮亏-{abs(amount):.0f}元"
        else:
            pnl_note = ""
        lines.append(f"{emoji} {name}  成本{cost_s}  现价{px_s}  {pct_s}{pnl_note}")
    return lines


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
) -> list[str]:
    lines: list[str] = ["🚨 买卖信号"]
    if not raw_buy and not raw_sell:
        lines.append("暂无买卖信号")
        return lines
    for sig in raw_buy:
        trigger = _signal_trigger_text(sig)
        px = _fmt_price(sig.price if sig.price > 0 else None)
        lines.append(f"{_RED} 买入：{sig.name}    触发「{trigger}」现价{px}")
    for sig in raw_sell:
        trigger = _signal_trigger_text(sig)
        px = _fmt_price(sig.price if sig.price > 0 else None)
        lines.append(f"{_GREEN} 卖出：{sig.name}    触发「{trigger}」现价{px}")
    return lines


def build_during_market_push(
    payload: dict,
    *,
    timestamp: str,
    raw_buy: list[TradeSignal] | None = None,
    raw_sell: list[TradeSignal] | None = None,
) -> str:
    """生成智能盯盘完整推送正文（含标题行）。"""
    payload = merge_payload_holdings(dict(payload))
    hm, day = _parse_header_time(timestamp)
    lines: list[str] = [f"📡 智能盯盘 {hm}  {day}", ""]

    lines.append(_SECTION.format(title="大盘"))
    index_lines = _format_index_lines(payload)
    if index_lines:
        lines.extend(index_lines)
    else:
        lines.append("暂无指数数据")
    lines.append(_format_turnover_line(payload))
    lines.append("")

    concept = payload.get(BOARD_CONCEPT) or {}
    industry = payload.get(BOARD_INDUSTRY) or {}
    board_specs = (
        (BOARD_CONCEPT, "概念·流入 TOP10", "资金流入榜", "fund"),
        (BOARD_CONCEPT, "概念·流出 TOP10", "资金流出榜", "fund"),
        (BOARD_CONCEPT, "概念·涨幅 TOP10", "涨幅榜", "gain"),
        (BOARD_CONCEPT, "概念·跌幅 TOP10", "跌幅榜", "gain"),
        (BOARD_INDUSTRY, "行业·流入 TOP10", "资金流入榜", "fund"),
        (BOARD_INDUSTRY, "行业·流出 TOP10", "资金流出榜", "fund"),
        (BOARD_INDUSTRY, "行业·涨幅 TOP10", "涨幅榜", "gain"),
        (BOARD_INDUSTRY, "行业·跌幅 TOP10", "跌幅榜", "gain"),
    )
    for section, title, key, kind in board_specs:
        block = concept if section == BOARD_CONCEPT else industry
        rows = section_board_rows({section: block}, section, key, limit=10) if isinstance(block, dict) else []
        _format_theme_board_section(lines, title=title, rows=rows, kind=kind)

    lines.append(_SECTION.format(title="自选股"))
    wl = _format_watchlist_lines(payload)
    lines.extend(wl if wl else ["暂无自选股"])
    lines.append("")

    lines.append(_SECTION.format(title="持仓股"))
    hl = _format_holding_lines(payload)
    lines.extend(hl if hl else ["暂无持仓"])
    lines.append("")

    lines.extend(_format_signal_lines(raw_buy or [], raw_sell or []))
    return "\n".join(lines).rstrip()
