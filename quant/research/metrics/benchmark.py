"""基准对比：拉取指数日线，对齐回测权益曲线计算超额收益。

默认基准沪深300（000300）。指数日线用 ``index_zh_a_hist``（东财，可回溯全历史）。
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from quant.backtest.broker import SimBroker


def _fetch_index_closes(symbol: str, start_yyyymmdd: str, end_yyyymmdd: str) -> dict[str, float]:
    """返回 {YYYY-MM-DD: 收盘价}；网络失败返回空 dict。"""
    try:
        import akshare as ak

        df = ak.index_zh_a_hist(
            symbol=str(symbol),
            period="daily",
            start_date=start_yyyymmdd,
            end_date=end_yyyymmdd,
        )
    except Exception:
        return {}

    date_col = None
    close_col = None
    for c in df.columns:
        cs = str(c)
        if "日期" in cs or "date" in cs.lower():
            date_col = c
        if "收盘" in cs or cs.lower() == "close":
            close_col = c
    if date_col is None or close_col is None:
        return {}

    out: dict[str, float] = {}
    for d, c in zip(df[date_col].tolist(), df[close_col].tolist()):
        try:
            ds = str(d).strip()[:10]
            cv = float(c)
        except (TypeError, ValueError):
            continue
        if ds and cv > 0:
            out[ds] = cv
    return out


def _yyyy_mmdd_to_yyyymmdd(s: str) -> str:
    return s.replace("-", "") if s else ""


def compute_benchmark_metrics(
    broker: SimBroker,
    *,
    benchmark_symbol: str = "000300",
    trading_days: int = 0,
) -> dict[str, Any]:
    """对齐权益曲线日期，计算基准收益/Sharpe 与超额收益。

    返回：
        benchmark_symbol / benchmark_total_return / benchmark_annualized_return /
        benchmark_sharpe / excess_return_vs_benchmark / tracking_error / beta
    网络失败时基准字段置 None，不影响主指标。
    """
    curve = broker.equity_curve
    if len(curve) < 2:
        return {"benchmark_symbol": benchmark_symbol}

    dates = [pt["date"] for pt in curve]
    start = _yyyy_mmdd_to_yyyymmdd(dates[0])
    end = _yyyy_mmdd_to_yyyymmdd(dates[-1])
    closes = _fetch_index_closes(benchmark_symbol, start, end)
    if len(closes) < 2:
        return {"benchmark_symbol": benchmark_symbol}

    # 对齐：为每个权益日取对应基准收盘（缺失用最近一次前值填充）。
    # 关键：策略与基准必须共用同一批日期，否则超额收益/beta 的时点会错位。
    # 基准无前值可填的起始日（早于基准首个数据点）整段丢弃，策略侧同步丢弃。
    sorted_bdates = sorted(closes)
    aligned: list[float] = []
    aligned_equities: list[float] = []
    bi = 0  # sorted_bdates 上的游标，随权益日单调前进，避免每日 O(n) 回扫
    prev = None
    for d, pt in zip(dates, curve):
        while bi < len(sorted_bdates) and sorted_bdates[bi] <= d:
            prev = closes[sorted_bdates[bi]]
            bi += 1
        if prev is None:
            continue  # 基准尚无数据，策略侧同步跳过
        aligned.append(prev)
        aligned_equities.append(float(pt["equity"]))

    if len(aligned) < 2:
        return {"benchmark_symbol": benchmark_symbol}

    # 策略与基准日收益逐日成对构造：任一侧无效则两侧同时跳过，保证严格同期，
    # 否则 tracking_error / beta 会拿错位的两条序列做协方差。
    bench_rets: list[float] = []
    strat_rets: list[float] = []
    for i in range(1, len(aligned)):
        b_prev, b_cur = aligned[i - 1], aligned[i]
        s_prev, s_cur = aligned_equities[i - 1], aligned_equities[i]
        if b_prev <= 0 or s_prev <= 0:
            continue
        bench_rets.append(b_cur / b_prev - 1.0)
        strat_rets.append(s_cur / s_prev - 1.0)

    if not bench_rets:
        return {"benchmark_symbol": benchmark_symbol}

    bench_total = aligned[-1] / aligned[0] - 1.0
    n_days = trading_days or max(len(bench_rets), 1)
    bench_ann = (1 + bench_total) ** (252 / max(n_days, 1)) - 1 if n_days else 0.0

    mean_br = sum(bench_rets) / len(bench_rets)
    var = sum((r - mean_br) ** 2 for r in bench_rets) / max(len(bench_rets) - 1, 1)
    bench_vol = math.sqrt(var) * math.sqrt(252)
    bench_sharpe = (bench_ann / bench_vol) if bench_vol > 1e-9 else 0.0

    # 超额收益 = 策略总收益 - 基准总收益（几何口径，非日超额算术累加）；
    # 两者都取 aligned 区间的首尾，保证同起止时点。
    excess = None
    tracking_error = None
    beta = None
    if strat_rets:
        ex = [s - b for s, b in zip(strat_rets, bench_rets)]
        strat_total = (
            aligned_equities[-1] / aligned_equities[0] - 1.0
            if aligned_equities[0] > 0
            else 0.0
        )
        excess = strat_total - bench_total
        ex_mean = sum(ex) / len(ex)
        ex_var = sum((x - ex_mean) ** 2 for x in ex) / max(len(ex) - 1, 1)
        tracking_error = math.sqrt(max(0.0, ex_var)) * math.sqrt(252)
        # beta = cov(strat, bench) / var(bench)
        sm = sum(strat_rets) / len(strat_rets)
        cov = sum((s - sm) * (b - mean_br) for s, b in zip(strat_rets, bench_rets)) / max(len(strat_rets) - 1, 1)
        beta = cov / var if var > 1e-9 else 0.0

    return {
        "benchmark_symbol": benchmark_symbol,
        "benchmark_total_return": round(bench_total, 4),
        "benchmark_annualized_return": round(bench_ann, 4),
        "benchmark_sharpe_ratio": round(bench_sharpe, 4),
        "excess_return_vs_benchmark": round(excess, 4) if excess is not None else None,
        "tracking_error": round(tracking_error, 4) if tracking_error is not None else None,
        "beta": round(beta, 4) if beta is not None else None,
    }
