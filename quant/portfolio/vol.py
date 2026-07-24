"""波动 / ATR 估计，供风险预算仓位使用。"""

from __future__ import annotations

from quant.scoring.tech_indicators import hist_close, hist_rows_sorted


def _row_hlc(row: dict) -> tuple[float | None, float | None, float | None]:
    high = None
    low = None
    for k in ("最高", "high", "最高价"):
        if row.get(k) is not None:
            try:
                high = float(row[k])
                break
            except (TypeError, ValueError):
                pass
    for k in ("最低", "low", "最低价"):
        if row.get(k) is not None:
            try:
                low = float(row[k])
                break
            except (TypeError, ValueError):
                pass
    close = hist_close(row)
    return high, low, close


def atr_pct(stock: dict, *, lookback: int = 14) -> float | None:
    """ATR% = ATR / 最新收盘 × 100。缺高低价时回退到收盘波动。"""
    rows = hist_rows_sorted(stock.get("历史行情"))
    if len(rows) < max(lookback + 1, 5):
        return realized_vol_pct(stock, lookback=lookback)

    trs: list[float] = []
    prev_close: float | None = None
    for row in rows:
        high, low, close = _row_hlc(row)
        if close is None or close <= 0:
            continue
        if high is None or low is None or high < low:
            if prev_close is not None and prev_close > 0:
                trs.append(abs(close - prev_close))
            prev_close = close
            continue
        tr = high - low
        if prev_close is not None:
            tr = max(tr, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = close

    if len(trs) < lookback:
        return realized_vol_pct(stock, lookback=lookback)
    atr = sum(trs[-lookback:]) / lookback
    last_close = hist_close(rows[-1]) or prev_close
    if not last_close or last_close <= 0:
        return None
    return atr / last_close * 100


def realized_vol_pct(stock: dict, *, lookback: int = 14) -> float | None:
    """近 lookback 日收盘涨跌幅标准差（%）。"""
    rows = hist_rows_sorted(stock.get("历史行情"))
    closes: list[float] = []
    for row in rows:
        c = hist_close(row)
        if c is not None and c > 0:
            closes.append(c)
    if len(closes) < lookback + 1:
        return None
    rets = [
        (closes[i] - closes[i - 1]) / closes[i - 1] * 100
        for i in range(-lookback, 0)
    ]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return var ** 0.5


def stock_volatility_pct(stock: dict, *, lookback: int = 14, floor: float = 1.0) -> float:
    """仓位用波动代理：优先 ATR%，否则实现波动；有下限防除零。"""
    atr = atr_pct(stock, lookback=lookback)
    if atr is not None and atr > 0:
        return max(floor, atr)
    vol = realized_vol_pct(stock, lookback=lookback)
    if vol is not None and vol > 0:
        return max(floor, vol)
    return max(floor, 3.0)
