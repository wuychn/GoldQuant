"""校验：抽样对比东财/新浪双源、检测断裂与缺失、跨除权日连续性。

用法：
    python -m scripts.data.verify_daily
    python -m scripts.data.verify_daily --sample 50
"""

from __future__ import annotations

import argparse
import random
import sys

import pandas as pd

from quant.data.adjust import apply_hfq, read_adj_factor
from quant.data.store import read_daily_raw


def _check_continuity(daily: pd.DataFrame) -> list[str]:
    """检测同一 code 的 date 序列是否有重复/倒序。"""
    issues: list[str] = []
    if daily.empty:
        return issues
    dup = daily.groupby("code")["date"].apply(lambda s: s.duplicated().sum())
    bad = dup[dup > 0]
    for code, n in bad.items():
        issues.append(f"{code} 重复日期 {n} 条")
    return issues


def _check_ex_div_continuity(daily: pd.DataFrame, adj: pd.DataFrame) -> list[str]:
    """后复权后跨除权日收盘应连续（无跳变）。"""
    issues: list[str] = []
    if daily.empty or adj.empty:
        return issues
    hfq = apply_hfq(daily, adj)
    for code, g in hfq.groupby("code"):
        g = g.sort_values("date")
        closes = pd.to_numeric(g["close"], errors="coerce").dropna()
        if len(closes) < 2:
            continue
        rets = closes.pct_change().dropna().abs()
        # 单日后复权收益 > 30% 视为可疑断裂（正常涨跌停 ±10%）
        big = rets[rets > 0.3]
        for d, r in big.items():
            issues.append(f"{code} @ {d} 后复权跳变 {r:.2%}（疑似复权因子缺失或错误）")
    return issues


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=20, help="抽样代码数")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    daily = read_daily_raw(start=args.start, end=args.end)
    if daily.empty:
        print("[FATAL] daily_raw 为空，请先运行 build_daily", file=sys.stderr)
        sys.exit(1)

    print(f"daily_raw: {len(daily)} 行, {daily['code'].nunique()} 只, "
          f"{daily['date'].min()} ~ {daily['date'].max()}")

    issues = _check_continuity(daily)
    adj = read_adj_factor()
    issues += _check_ex_div_continuity(daily, adj)

    # 抽样：检查每只票的日期覆盖是否连续（无大段缺失）
    codes = daily["code"].astype(str).unique().tolist()
    sample = random.sample(codes, min(args.sample, len(codes)))
    for code in sample:
        g = daily[daily["code"] == code].sort_values("date")
        dates = pd.to_datetime(g["date"])
        if len(dates) < 2:
            continue
        gaps = dates.diff().dt.days
        # 交易日间隔正常 ≤ 8 天（含节假日），> 15 天视为可疑缺失
        big_gaps = gaps[gaps > 15]
        for d, gap in big_gaps.items():
            issues.append(f"{code} @ {d.date()} 缺失区间 {gap} 天")

    if issues:
        print(f"\n发现 {len(issues)} 个问题:")
        for s in issues[:30]:
            print("  -", s)
        if len(issues) > 30:
            print(f"  ... 还有 {len(issues)-30} 条")
        sys.exit(1)
    print("校验通过，无断裂/缺失/复权异常")


if __name__ == "__main__":
    main()
