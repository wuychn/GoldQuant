"""跨日归档摘要：仅提供昨日复盘等叙述参考，不含概念板块判定（由 engine_brief 负责）。"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from quant.scoring.theme_tracker import _concept_tracker_cfg, snapshot_boards
from quant.store.paths import daily_raw, daily_review

_SH_TZ = ZoneInfo("Asia/Shanghai")

_MODES_WITH_CROSS_DAY = frozenset(
    {"pre_market", "during_market", "post_market_lunch", "post_market_evening"}
)


def _read_json(path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _find_latest_date_with(*names: str, start: date | None = None, max_scan: int = 14) -> str | None:
    """从 start 日（默认昨天）向前找，存在任一 raw/review 文件的最近日期。"""
    d = start or (datetime.now(_SH_TZ).date() - timedelta(days=1))
    for _ in range(max_scan):
        ds = d.isoformat()
        for name in names:
            if name.endswith(".md"):
                if daily_review(name, ds).is_file():
                    return ds
            elif daily_raw(name, ds).is_file():
                return ds
        d -= timedelta(days=1)
    return None


def _strip_push_header(text: str) -> str:
    text = text.strip()
    if text.startswith("【"):
        m = re.match(r"^【[^】]+】[^\n]*\n+", text)
        if m:
            return text[m.end() :].strip()
    return text


def _read_review_excerpt(review_name: str, date_str: str, *, max_chars: int) -> str:
    path = daily_review(review_name, date_str)
    if not path.is_file():
        return ""
    try:
        text = _strip_push_header(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    text = text.strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return "…" + text[-max_chars:]


def _load_evening_raw(date_str: str) -> dict | None:
    return _read_json(daily_raw("evening.json", date_str))


def format_concept_rotation(*, lookback: int | None = None) -> str:
    """近 N 个 evening 归档的概念榜时间线 + 轮动摘要。"""
    cfg = _concept_tracker_cfg()
    n = lookback or int(cfg.get("lookback_days", 10))
    snapshots: list[tuple[str, set[str], set[str]]] = []
    d = datetime.now(_SH_TZ).date() - timedelta(days=1)

    for _ in range(45):
        if len(snapshots) >= n:
            break
        ds = d.isoformat()
        payload = _load_evening_raw(ds)
        if payload:
            gain, fund = snapshot_boards(payload, limit=int(cfg.get("board_limit", 10)))
            snapshots.append((ds, gain, fund))
        d -= timedelta(days=1)

    if not snapshots:
        return "暂无 evening.json 归档，无法生成概念轮动时间线。"

    snapshots.reverse()
    lines = ["近{}个交易日概念榜（来源: daily/raw/evening.json）:".format(len(snapshots))]
    concept_days: dict[str, int] = {}

    for ds, gain, fund in snapshots:
        g5 = sorted(gain)[:5]
        f5 = sorted(fund)[:5]
        lines.append(f"- {ds} 涨幅前5: {'、'.join(g5) if g5 else '无'}")
        lines.append(f"  资金前5: {'、'.join(f5) if f5 else '无'}")
        for c in gain | fund:
            concept_days[c] = concept_days.get(c, 0) + 1

    persistent = sorted([c for c, cnt in concept_days.items() if cnt >= 2], key=lambda c: (-concept_days[c], c))
    if persistent:
        lines.append("多次上榜（轮动中偏持续）: " + "、".join(persistent[:12]))

    if len(snapshots) >= 2:
        prev_gain, prev_fund = snapshots[-2][1], snapshots[-2][2]
        last_gain, last_fund = snapshots[-1][1], snapshots[-1][2]
        new_gain = sorted(last_gain - prev_gain)
        new_fund = sorted(last_fund - prev_fund)
        drop_gain = sorted(prev_gain - last_gain)
        if new_gain or new_fund:
            lines.append(
                "最近一日新增强势: "
                + "、".join(new_gain[:6])
                + ("；资金新晋: " + "、".join(new_fund[:6]) if new_fund else "")
            )
        if drop_gain:
            lines.append("最近一日退出涨幅前榜: " + "、".join(drop_gain[:6]))

    return "\n".join(lines)


def _load_today_trades(*, date_str: str | None = None) -> tuple[str, list[dict]]:
    from quant.store.snapshot import daily_trades_path

    ds = date_str or datetime.now(_SH_TZ).date().isoformat()
    path = daily_trades_path("executed.json", ds)
    if not path.is_file():
        return ds, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ds, []
    if not isinstance(data, list):
        return ds, []
    rows = [r for r in data if isinstance(r, dict)]
    return ds, rows


def _resolve_mark_price(stock: dict) -> float | None:
    from quant.scoring.tech_indicators import hist_close, hist_rows_sorted, quote_last_price

    price = quote_last_price(stock)
    if price is not None and price > 0:
        return price
    hist = hist_rows_sorted(stock.get("历史行情"))
    if hist:
        close = hist_close(hist[-1])
        if close is not None and close > 0:
            return close
    try:
        buy = float(stock.get("买入价", 0) or 0)
    except (TypeError, ValueError):
        buy = 0.0
    return buy if buy > 0 else None


def _summarize_trade_fees(rows: list[dict]) -> tuple[float, float, float, float, float]:
    commission = stamp = transfer = buy_amount = sell_amount = 0.0
    for r in rows:
        try:
            commission += float(r.get("佣金") or 0)
            stamp += float(r.get("印花税") or 0)
            transfer += float(r.get("过户费") or 0)
            amount = float(r.get("成交额") or 0)
        except (TypeError, ValueError):
            continue
        action = str(r.get("方向") or "").strip()
        if action == "买入":
            buy_amount += amount
        elif action == "卖出":
            sell_amount += amount
    return commission, stamp, transfer, buy_amount, sell_amount


def format_today_trades(*, date_str: str | None = None) -> str:
    """当日成交摘要，供晚间操作复盘引用。"""
    ds, rows = _load_today_trades(date_str=date_str)
    if not rows:
        return f"{ds} 今日无买卖成交。"

    parts: list[str] = []
    for r in rows:
        action = str(r.get("方向") or "").strip()
        code = r.get("股票代码") or ""
        name = r.get("股票名称") or ""
        qty = r.get("股数") or 0
        price = r.get("成交价")
        pnl = r.get("已实现盈亏")
        reason = str(r.get("理由") or "")[:40]
        sell_type = str(r.get("卖出类型") or "").strip()
        extra = f" [{sell_type}]" if sell_type else ""
        pnl_s = ""
        if pnl is not None and str(pnl).strip() != "":
            try:
                pnl_s = f" 盈亏{float(pnl):+.2f}元"
            except (TypeError, ValueError):
                pass
        price_s = f" @{price}" if price is not None else ""
        parts.append(
            f"{action} {name}({code}) {int(qty or 0) // 100}手{price_s}{pnl_s}{extra} {reason}".strip()
        )
    return f"{ds} 成交（共{len(parts)}笔）：" + "；".join(parts)


def _compute_today_pnl_components(
    holdings: list[dict],
    *,
    date_str: str,
) -> tuple[float, float, float]:
    """返回 (当日盈亏, 成交已实现盈亏, 持仓较昨收变动)。"""
    from quant.scoring.tech_indicators import stock_daily_change_pct
    from quant.store.state import sum_today_realized_pnl

    realized = sum_today_realized_pnl(date_str)
    holding_daily = 0.0
    for h in holdings:
        if not isinstance(h, dict):
            continue
        qty = int(h.get("持仓股数", 0) or 0)
        if qty <= 0:
            continue
        price = _resolve_mark_price(h)
        chg = stock_daily_change_pct(h)
        if price is None or chg is None:
            continue
        holding_daily += qty * price * chg / 100.0
    return realized + holding_daily, realized, holding_daily


def format_today_pnl_summary(
    payload: dict | None = None,
    *,
    date_str: str | None = None,
    sync_account: bool = True,
) -> str:
    """当日盈亏与账户资金（盘中/午间/晚间共用）。"""
    from quant.narrative.holdings_context import holdings_for_pnl
    from quant.store.state import refresh_account_market_value

    ds = date_str or datetime.now(_SH_TZ).date().isoformat()
    holdings = holdings_for_pnl(payload)
    if sync_account:
        acc = refresh_account_market_value(holdings)
    else:
        from quant.store.state import compute_holdings_market_value, get_account

        acc = dict(get_account())
        cash = float(acc.get("可用资金", 0) or 0)
        mv = compute_holdings_market_value(holdings)
        acc["持仓市值"] = round(mv, 4)
        acc["总资产"] = round(cash + mv, 4)

    _, rows = _load_today_trades(date_str=ds)
    daily_pnl, realized, holding_daily = _compute_today_pnl_components(holdings, date_str=ds)

    lines = [f"当日盈亏：{daily_pnl:+.2f}元"]
    if rows or holding_daily:
        parts: list[str] = []
        if rows:
            parts.append(f"成交{realized:+.2f}元")
        if holding_daily:
            parts.append(f"持仓较昨收{holding_daily:+.2f}元")
        if parts:
            lines.append(f"（{' + '.join(parts)}）")

    if rows:
        comm, stamp, transfer, buy_amt, sell_amt = _summarize_trade_fees(rows)
        lines.append(
            f"今日成交 {len(rows)} 笔：买入{buy_amt:.2f}元 卖出{sell_amt:.2f}元"
        )
        lines.append(
            f"今日费用：佣金{comm:.2f}元 印花税{stamp:.2f}元 过户费{transfer:.2f}元"
        )
        for r in rows:
            action = str(r.get("方向") or "").strip()
            name = r.get("股票名称") or ""
            code = r.get("股票代码") or ""
            qty = int(r.get("股数") or 0)
            price = r.get("成交价")
            price_s = f" @{price}" if price is not None else ""
            if action == "买入":
                lines.append(
                    f"· 买入 {name}({code}) {qty}股{price_s} "
                    f"佣金{float(r.get('佣金') or 0):.2f}元"
                )
            else:
                try:
                    pnl_f = float(r.get("已实现盈亏") or 0)
                except (TypeError, ValueError):
                    pnl_f = 0.0
                lines.append(f"· 卖出 {name}({code}) {qty}股{price_s} 盈亏{pnl_f:+.2f}元")
    else:
        lines.append("今日无成交")

    cash = float(acc.get("可用资金", 0) or 0)
    position_mv = float(acc.get("持仓市值", 0) or 0)
    total_assets = float(acc.get("总资产", 0) or 0)
    lines.append(
        f"账户：可用{cash:.2f}元 | 持仓市值{position_mv:.2f}元 | 总资产{total_assets:.2f}元"
    )
    if abs(cash + position_mv - total_assets) > 0.02:
        lines.append(
            f"· 资金校验异常：可用+持仓市值={cash + position_mv:.2f}元，"
            f"总资产={total_assets:.2f}元"
        )
    return "\n".join(lines)


def format_yesterday_trades(*, date_str: str | None = None) -> str:
    from quant.store.snapshot import daily_trades_path

    if date_str:
        candidates = [date_str]
    else:
        candidates = []
        d = datetime.now(_SH_TZ).date() - timedelta(days=1)
        for _ in range(14):
            candidates.append(d.isoformat())
            d -= timedelta(days=1)

    rows: list[dict] = []
    used_date = ""
    for ds in candidates:
        path = daily_trades_path("executed.json", ds)
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list) and data:
            rows = [r for r in data if isinstance(r, dict)]
            used_date = ds
            break

    if not rows:
        return ""

    parts: list[str] = []
    for r in rows[-8:]:
        action = r.get("方向") or ""
        code = r.get("股票代码") or ""
        name = r.get("股票名称") or ""
        reason = str(r.get("理由") or "")[:40]
        parts.append(f"{action} {name}({code}) {reason}".strip())

    if not parts:
        return ""
    return f"{used_date} 成交（最近{len(parts)}笔）: " + "；".join(parts)


def build_cross_day_context(mode: str) -> str:
    """按模式组装跨日叙述参考（复盘文案），不含交易决策。"""
    if mode not in _MODES_WITH_CROSS_DAY:
        return ""

    sections: list[str] = []

    prev_ds = _find_latest_date_with("evening.md", "evening.json")
    if prev_ds:
        excerpt = _read_review_excerpt("evening.md", prev_ds, max_chars=2000 if mode == "pre_market" else 1400)
        if excerpt:
            sections.append(
                f"昨日复盘摘录（{prev_ds}，勿抄标题、勿据此重判概念板块）：\n{excerpt}"
            )

    if mode == "post_market_lunch":
        today = datetime.now(_SH_TZ).date().isoformat()
        pre_excerpt = _read_review_excerpt("pre_market.md", today, max_chars=800)
        if pre_excerpt:
            sections.append(f"今日盘前推送摘录（{today}，勿抄标题）：\n{pre_excerpt}")

    return "\n\n".join(sections)
