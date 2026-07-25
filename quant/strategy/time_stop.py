"""时间/横盘止损：持仓若干交易日后涨幅有限且近期振幅收窄则离场（非固定止盈）。"""

from __future__ import annotations

from datetime import date
from typing import Any

from quant.scoring.tech_indicators import hist_closes, hist_rows_sorted
from quant.timeutil import cn_now


def _hist_row_date(row: dict) -> str:
    return str(row.get("日期") or row.get("date") or "").strip()[:10]


def parse_buy_date(holding: dict) -> date | None:
    ts = str(holding.get("买入时间", "")).strip()
    if not ts:
        return None
    try:
        return date.fromisoformat(ts[:10])
    except ValueError:
        return None


def trading_days_since_buy(stock: dict, buy_date: date) -> int:
    """买入日之后的已持交易日数。

    优先用交易日历（``tool_trade_date_hist_sina``）按 buy_date → 最后 K 线日计数，
    避免依赖个股历史行情的完整性（停牌/数据缺失会让 K 线根数失真）。
    「最后日」取自历史行情最后一根 K 线（回测中即快照日，实盘中即当日/最近交易日）；
    历史行情缺失时回退到当前日历日。
    """
    last_date = _last_hist_date(stock)
    today = last_date or cn_now().date()
    try:
        from quant.data.calendar import trading_days_since

        n = trading_days_since(buy_date, today)
        if n > 0:
            return n
        # 日历返回 0（buy_date >= today 或日历不可用）→ 走 K 线兜底
    except Exception:
        pass

    n = 0
    for row in hist_rows_sorted(stock.get("历史行情")):
        ds = _hist_row_date(row)
        if len(ds) < 10:
            continue
        try:
            d = date.fromisoformat(ds)
        except ValueError:
            continue
        if d > buy_date:
            n += 1
    return n


def _last_hist_date(stock: dict) -> date | None:
    rows = hist_rows_sorted(stock.get("历史行情"))
    if not rows:
        return None
    ds = _hist_row_date(rows[-1])
    if len(ds) < 10:
        return None
    try:
        return date.fromisoformat(ds)
    except ValueError:
        return None


def _window_range_and_net(closes: list[float], window: int) -> tuple[float | None, float | None]:
    if len(closes) < window or window < 2:
        return None, None
    seg = closes[-window:]
    lo, hi = min(seg), max(seg)
    mid = sum(seg) / len(seg)
    if mid <= 0:
        return None, None
    range_pct = (hi - lo) / mid * 100
    start = seg[0]
    if start <= 0:
        return range_pct, None
    net_pct = abs(seg[-1] - start) / start * 100
    return range_pct, net_pct


def time_stop_triggers_sell(
    stock: dict,
    sell_cfg: dict[str, Any] | None,
    *,
    pnl_pct: float,
    buy_date: date | None,
) -> tuple[bool, str]:
    """时间/横盘止损：持够天数 + 累计涨幅未明显放大 + 近端窄幅横盘。

    非固定止盈：浮盈超过 max_pnl_pct 时不触发，让强趋势继续走。
    深亏由 stop_loss_pct 优先处理（本规则要求 pnl >= min_pnl_pct）。
    """
    cfg = dict((sell_cfg or {}).get("time_stop") or {})
    if not cfg.get("enabled", True):
        return False, ""
    if buy_date is None:
        return False, ""

    min_days = int(cfg.get("min_hold_trading_days", 5))
    min_pnl = float(cfg.get("min_pnl_pct", -3.0))
    max_pnl = float(cfg.get("max_pnl_pct", 5.0))
    lookback = int(cfg.get("sideways_lookback_days", 5))
    max_range = float(cfg.get("max_range_pct", 5.5))
    max_net = float(cfg.get("max_net_move_pct", 3.0))

    held = trading_days_since_buy(stock, buy_date)
    if held < min_days:
        return False, ""

    if pnl_pct > max_pnl:
        return False, ""
    if pnl_pct < min_pnl:
        return False, ""

    closes = hist_closes(stock.get("历史行情"))
    range_pct, net_pct = _window_range_and_net(closes, lookback)
    if range_pct is None:
        return False, ""
    if range_pct > max_range:
        return False, ""
    if net_pct is not None and net_pct > max_net:
        return False, ""

    return (
        True,
        f"持{held}日浮盈{pnl_pct:.2f}%未超{max_pnl}%；近{lookback}日振幅{range_pct:.2f}%≤{max_range}%"
        + (f"、净波动{net_pct:.2f}%≤{max_net}%" if net_pct is not None else ""),
    )
