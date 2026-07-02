"""盘中买入：分时走势 / 均价线 / 资金改善过滤。"""

from __future__ import annotations

import re
from typing import Any

from quant.constants import BUY_KIND_ASCENT, BUY_KIND_PULLBACK
from quant.market.fund_flow import intraday_main_net_wan
from quant.scoring.tech_indicators import (
    quote_avg_price,
    quote_change_pct,
    quote_last_price,
    to_float,
)
from quant.store.intraday_fund_track import net_flow_improving

_HMS_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def is_continuous_auction_minute(ts: object) -> bool:
    """A 股连续竞价时段：09:30–11:30、13:00–15:00（含端点）。"""
    m = _HMS_RE.search(str(ts or ""))
    if not m:
        return False
    h, mi = int(m.group(1)), int(m.group(2))
    tot = h * 60 + mi
    return (9 * 60 + 30 <= tot <= 11 * 60 + 30) or (13 * 60 <= tot <= 15 * 60)


def session_minute_bars(stock: dict) -> list[dict]:
    """连续竞价分钟 K（09:30 起，含成交量）。"""
    raw = stock.get("分钟行情") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        ts = str(row.get("时间") or "")
        if not is_continuous_auction_minute(ts):
            continue
        close = to_float(row.get("收盘") or row.get("最新价"))
        if close is None or close <= 0:
            continue
        vol = to_float(row.get("成交量")) or 0.0
        opn = to_float(row.get("开盘")) or close
        out.append({"open": opn, "close": close, "vol": vol, "time": ts})
    return out


def _session_minute_bars(stock: dict) -> list[dict]:
    return session_minute_bars(stock)


def _vwap(bars: list[dict]) -> float | None:
    num = 0.0
    den = 0.0
    for b in bars:
        v = b["vol"]
        if v <= 0:
            continue
        num += b["close"] * v
        den += v
    return num / den if den > 0 else None


def _minute_close_slope_pct(closes: list[float]) -> float | None:
    """首尾收盘涨跌幅(%)，衡量近 N 分钟整体斜率（震荡向上：允许中间回撤）。"""
    if len(closes) < 2:
        return None
    base = closes[0]
    if base <= 0:
        return None
    return (closes[-1] - base) / base * 100


def _ols_slope_pct(closes: list[float]) -> float | None:
    """最小二乘斜率：窗口首尾预测涨跌(%)，抗单根异常 K。"""
    n = len(closes)
    if n < 2 or closes[0] <= 0:
        return None
    x_mean = (n - 1) / 2.0
    y_mean = sum(closes) / n
    num = sum((i - x_mean) * (closes[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den <= 0:
        return None
    slope_per_bar = num / den
    return slope_per_bar * (n - 1) / closes[0] * 100


def _momentum_groups_cfg(intra: dict[str, Any]) -> dict[str, Any]:
    mg = dict(intra.get("momentum_groups") or {})
    mg.setdefault("enabled", True)
    mg.setdefault("slope_minutes", 5)
    mg.setdefault("slope_warmup_bars", 10)
    mg.setdefault("min_endpoint_slope_pct", 0.0)
    mg.setdefault("min_endpoint_slope_pct_early", -0.05)
    mg.setdefault("min_ols_slope_pct", 0.0)
    mg.setdefault("pullback_min_endpoint_slope_pct", -0.15)
    mg.setdefault("pullback_min_ols_slope_pct", -0.15)
    mg.setdefault("ascent_relax_on_strong_day", True)
    mg.setdefault("ascent_relax_min_day_chg", 5.0)
    return mg


def _strength_signal_ok(
    stock: dict,
    intra: dict[str, Any],
    *,
    code: str,
    last: float,
    vwap: float | None,
) -> tuple[bool, str]:
    chg = quote_change_pct(stock)
    skip_min_chg = float(intra.get("skip_strength_min_day_chg", 5.0))
    strong_day = (
        chg is not None
        and chg >= skip_min_chg
        and vwap
        and last >= vwap
    )
    # 主力资金统一口径：大单流入 − 大单流出（万元）
    net = intraday_main_net_wan(stock)
    min_delta = float(intra.get("fund_improve_min_delta_wan", 50.0))
    net_ok = net is not None and net > 0
    improving = net_flow_improving(code, min_delta_wan=min_delta, current_net=net)
    if strong_day:
        return True, "当日强势"
    if net_ok:
        return True, "净流入"
    if improving:
        return True, "流出收敛"
    if net is not None and net < 0:
        return False, "净流出且未见改善"
    return False, "分时强势证据不足"


def _momentum_group2_ok(bars: list[dict], intra: dict[str, Any], *, buy_kind: str) -> tuple[bool, str]:
    mg = _momentum_groups_cfg(intra)
    if not bars:
        return False, "无分钟K"
    n = max(2, int(mg.get("slope_minutes", 5)))
    warmup = max(0, int(mg.get("slope_warmup_bars", 10)))
    early = len(bars) < warmup
    if buy_kind == BUY_KIND_PULLBACK:
        min_ep = float(mg.get("pullback_min_endpoint_slope_pct", -0.15))
        min_ols = float(mg.get("pullback_min_ols_slope_pct", -0.15))
    elif early:
        min_ep = float(mg.get("min_endpoint_slope_pct_early", -0.05))
        min_ols = float(mg.get("min_ols_slope_pct", 0.0))
    else:
        min_ep = float(mg.get("min_endpoint_slope_pct", 0.0))
        min_ols = float(mg.get("min_ols_slope_pct", 0.0))

    closes = [b["close"] for b in bars[-n:]]
    slope_ep = _minute_close_slope_pct(closes)
    slope_ols = _ols_slope_pct(closes)
    if slope_ep is not None and slope_ep >= min_ep:
        return True, f"近{n}分钟首尾斜率{slope_ep:.2f}%"
    if slope_ols is not None and slope_ols >= min_ols:
        return True, f"近{n}分钟OLS斜率{slope_ols:.2f}%"
    ep_s = f"{slope_ep:.2f}" if slope_ep is not None else "—"
    ols_s = f"{slope_ols:.2f}" if slope_ols is not None else "—"
    return False, f"动量不足(首尾{ep_s}% OLS{ols_s}%, 需≥{min_ep:.2f}/{min_ols:.2f}%)"


def _drawdown_from_high_cfg(intra: dict[str, Any]) -> dict[str, Any]:
    raw = intra.get("drawdown_from_high")
    if isinstance(raw, (int, float)):
        return {"default_pct": float(raw), "require_below_avg": True}
    return dict(raw or {})


def resolve_max_drop_from_high_pct(
    intra: dict[str, Any],
    *,
    buy_kind: str = "",
    day_chg: float | None = None,
) -> float:
    """按买点类型 / 当日强度分档解析距日内高点回撤上限(%)。"""
    dd = _drawdown_from_high_cfg(intra)
    by_kind = dd.get("by_buy_kind") or {}
    pct = float(by_kind.get(buy_kind) if buy_kind in by_kind else dd.get("default_pct", 3.0))
    strong_chg = float(dd.get("strong_day_chg_pct", 5.0))
    if day_chg is not None and day_chg >= strong_chg:
        pct = max(pct, float(dd.get("strong_day_pct", 5.0)))
    return pct


def _high_drawdown_blocks_buy(
    *,
    last: float,
    high: float,
    vwap: float | None,
    avg_margin_pct: float,
    intra: dict[str, Any],
    buy_kind: str,
    day_chg: float | None,
) -> tuple[bool, str]:
    dd = _drawdown_from_high_cfg(intra)
    max_dd = resolve_max_drop_from_high_pct(intra, buy_kind=buy_kind, day_chg=day_chg)
    drawdown = (last - high) / high * 100
    if drawdown >= -max_dd:
        return False, ""
    require_below = bool(dd.get("require_below_avg", True))
    if require_below and vwap:
        floor = vwap * (1 + avg_margin_pct / 100)
        if last >= floor:
            return False, ""
    return True, f"距日内高点回撤{abs(drawdown):.2f}%过大(上限{max_dd:.1f}%)"


def _intraday_cfg(buy_cfg: dict[str, Any] | None) -> dict[str, Any]:
    c = buy_cfg or {}
    intra = dict(c.get("intraday") or {})
    intra.setdefault("enabled", True)
    return intra


def _momentum_groups_ok(
    stock: dict,
    bars: list[dict],
    intra: dict[str, Any],
    *,
    code: str,
    last: float,
    vwap: float | None,
    buy_kind: str,
) -> tuple[bool, str]:
    """三组确认：组间 AND，组内 OR；上升途中有强势日可 1+(2|3)。"""
    mg = _momentum_groups_cfg(intra)
    if not mg.get("enabled", True):
        return True, ""

    ok2, note2 = _momentum_group2_ok(bars, intra, buy_kind=buy_kind)
    ok3, note3 = _strength_signal_ok(stock, intra, code=code, last=last, vwap=vwap)

    chg = quote_change_pct(stock)
    relax = (
        buy_kind == BUY_KIND_ASCENT
        and mg.get("ascent_relax_on_strong_day", True)
        and chg is not None
        and chg >= float(mg.get("ascent_relax_min_day_chg", 5.0))
    )
    if relax:
        if ok2 or ok3:
            part = note2 if ok2 else note3
            return True, f"强势日放宽({part})"
        return False, f"强势日仍须动量或资金({note2}; {note3})"

    if not ok2:
        return False, note2
    if not ok3:
        return False, note3
    return True, f"{note2}; {note3}"


def intraday_allows_buy(
    stock: dict,
    buy_cfg: dict[str, Any] | None = None,
    *,
    buy_kind: str = "",
    lightweight: bool = False,
) -> tuple[bool, str]:
    """盘中买入前置：位置 + 动量/资金分组 + 分档回撤红线。"""
    intra = _intraday_cfg(buy_cfg)
    if not intra.get("enabled", True):
        return True, ""

    last = quote_last_price(stock)
    if not last or last <= 0:
        return False, "无有效现价"

    code = str(stock.get("股票代码", "")).strip()
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    bars = _session_minute_bars(stock)
    avg = quote_avg_price(stock) or _vwap(bars)
    vwap = avg or _vwap(bars)
    margin = float(intra.get("avg_margin_pct", 0.0))
    chg = quote_change_pct(stock)

    # --- 组1：现价须在分时均价上方 ---
    if intra.get("require_above_avg", True):
        floor = vwap * (1 + margin / 100) if vwap else None
        if floor is None or last < floor:
            avg_s = f"{vwap:.2f}" if vwap else "—"
            return False, f"现价{last:.2f}低于分时均价{avg_s}"

    if lightweight:
        high = to_float(pk.get("最高"))
        if high and high > 0:
            blocked, msg = _high_drawdown_blocks_buy(
                last=last,
                high=high,
                vwap=vwap,
                avg_margin_pct=margin,
                intra=intra,
                buy_kind=buy_kind,
                day_chg=chg,
            )
            if blocked:
                return False, msg
        return True, "分时轻量再验通过"

    ok_groups, group_note = _momentum_groups_ok(
        stock,
        bars,
        intra,
        code=code,
        last=last,
        vwap=vwap,
        buy_kind=buy_kind,
    )
    if not ok_groups:
        return False, group_note

    high = to_float(pk.get("最高"))
    if high and high > 0:
        blocked, msg = _high_drawdown_blocks_buy(
            last=last,
            high=high,
            vwap=vwap,
            avg_margin_pct=margin,
            intra=intra,
            buy_kind=buy_kind,
            day_chg=chg,
        )
        if blocked:
            return False, msg

    min_chg = float(intra.get("min_day_change_pct", -1.5))
    if chg is not None and chg < min_chg:
        return False, f"当日涨幅{chg:.2f}%偏弱"

    # 主力资金统一口径：大单流入 − 大单流出（万元）；净流出过大拒买
    net = intraday_main_net_wan(stock)
    if net is not None:
        hard = float(intra.get("hard_net_outflow_wan", 8000))
        if net < -hard:
            return False, f"当日净流出{abs(net):.0f}万过大"

    return True, group_note or "分时确认通过"


def _intraday_weakness_cfg(sell_cfg: dict[str, Any] | None) -> dict[str, Any]:
    c = sell_cfg or {}
    weak = dict(c.get("intraday_weakness") or {})
    weak.setdefault("enabled", True)
    return weak


def _session_high(stock: dict, bars: list[dict]) -> float | None:
    pk = stock.get("盘口") if isinstance(stock.get("盘口"), dict) else {}
    high = to_float(pk.get("最高"))
    if high and high > 0:
        return high
    if not bars:
        return None
    return max(b["close"] for b in bars)


def intraday_weakness_triggers_sell(
    stock: dict,
    sell_cfg: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """盘中走弱卖出：冲高回落 + 有效跌破分时均价 + 近端分时确认。

    与买入 ``intraday_allows_buy`` 对称，用于持仓止盈/避险，不依赖破 5 日线。
    """
    cfg = _intraday_weakness_cfg(sell_cfg)
    if not cfg.get("enabled", True):
        return False, ""

    bars = _session_minute_bars(stock)
    min_bars = max(3, int(cfg.get("min_minute_bars", 8)))
    if len(bars) < min_bars:
        return False, ""

    last = quote_last_price(stock)
    if not last or last <= 0:
        return False, ""

    high = _session_high(stock, bars)
    if not high or high <= 0:
        return False, ""

    dd_pct = (last - high) / high * 100
    min_dd = float(cfg.get("max_drop_from_high_pct", 3.0))
    if dd_pct > -min_dd:
        return False, ""

    avg = quote_avg_price(stock) or _vwap(bars)
    if not avg or avg <= 0:
        return False, ""

    margin = float(cfg.get("avg_break_margin_pct", 0.15))
    ceiling = avg * (1 - margin / 100)
    if last >= ceiling:
        return False, ""

    if cfg.get("require_recent_weakness", True):
        lookback = max(3, int(cfg.get("lookback_minutes", 10)))
        recent = bars[-lookback:]
        if len(recent) >= 3:
            closes = [b["close"] for b in recent]
            drop_pct = (closes[-1] - closes[0]) / closes[0] * 100
            min_recent_drop = float(cfg.get("min_recent_drop_pct", 0.25))
            if drop_pct > -min_recent_drop:
                return False, ""

            down_steps = sum(1 for i in range(1, len(closes)) if closes[i] < closes[i - 1])
            min_down_ratio = float(cfg.get("min_down_bars_ratio", 0.55))
            if down_steps / (len(closes) - 1) < min_down_ratio:
                return False, ""

    return (
        True,
        f"距日内高点回撤{abs(dd_pct):.2f}%，现价{last:.2f}有效低于分时均价{avg:.2f}",
    )
