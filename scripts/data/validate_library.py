"""离线库完整性与正确性校验。

跑一遍即知数据是否完整、是否正确（含复权连续性——除权尖刺 bug 探测器）。

用法：
    poetry run python -m scripts.data.validate_library
    poetry run python -m scripts.data.validate_library --home ~/.quant
    poetry run python -m scripts.data.validate_library --start 2021-01-01 --end 2026-08-03

退出码：0 = 通过；1 = 发现问题（完整性问题多为 FAIL；覆盖类低值记 WARN，因停牌/次新
属合法缺日）。

检查项：
[1] 基础      calendar 天数 / daily_raw 行·码·日期区间 / name 非空率
[2] 完整性    adj_factor 覆盖率(<90% FAIL) / index_daily 000300·000905·000852(缺 FAIL) /
              per-code 日历覆盖(低 WARN)
[3] 正确性    重复 (code,date)(>0 FAIL) / 价格 sanity(close>0、high>=low、high/low 夹住
              open·close、volume·amount>=0)(违规 FAIL) / 复权跳空：仅查日历相邻交易日，
              且「后复权跳 >28% 而原料价未跳」才 FAIL（停牌复牌/次新大波动豁免）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common.progress_log import log_progress_done, log_progress_error, log_progress_start
from quant.data.schema import INDEX_DAILY_COLUMNS
from quant.data.store import (
    read_adj_factor,
    read_calendar,
    read_daily_raw,
    read_index_daily,
)
from quant.store.paths import override_quant_home

JUMP_PCT = 28.0          # 后复权单日跳空阈值：合法 A 股涨停/跌停 ≤20%，>28% 必为复权/数据 bug
ADJ_COVER_MIN = 0.90     # adj_factor 覆盖率红线
CAL_COVER_MIN = 0.90     # per-code 日历覆盖 WARN 线
INDEX_CODES = ("000300", "000905", "000852")
_SCOPE = "validate_library"

FAILS: list[str] = []
WARNS: list[str] = []


def _note(kind: str, msg: str) -> None:
    print(f"  [{kind}] {msg}")
    (FAILS if kind == "FAIL" else WARNS).append(msg)


def _check_index() -> None:
    for code in INDEX_CODES:
        try:
            df = read_index_daily(code)
            ok = df is not None and not df.empty and "close" in df.columns
        except Exception as e:  # noqa: BLE001
            ok, e = False, e
        if ok:
            print(f"  [OK]   index_daily {code}: {len(df)} 行")
        else:
            _note("FAIL", f"index_daily {code} 缺失或空")


def _calendar_next_map(cal: list[str]) -> dict[str, str]:
    days = sorted({str(d) for d in cal})
    return {days[i]: days[i + 1] for i in range(len(days) - 1)}


def adj_jump_suspects(
    daily: pd.DataFrame,
    adj: pd.DataFrame,
    cal: list[str] | None = None,
    *,
    jump_pct: float = JUMP_PCT,
) -> pd.DataFrame:
    """找出疑似复权 bug 的跳空行（供校验与单测）。

    条件（同时满足）：
    1. 库内上一行与本行是**日历上相邻交易日**（长停牌复牌豁免）；
    2. 后复权涨跌幅绝对值 > ``jump_pct``；
    3. 同期**不复权**涨跌幅绝对值 ≤ ``jump_pct``（原料也大跳 → 次新/涨跌停外波动，豁免）。

    真除权日：原料常大跳、后复权应平滑 → 不会进嫌疑；因子错接：原料平稳、后复权尖刺 → FAIL。
    """
    from quant.data.adjust import apply_hfq

    if daily.empty or adj is None or adj.empty:
        return pd.DataFrame()
    raw = daily.copy()
    raw["date"] = raw["date"].astype(str)
    raw["code"] = raw["code"].astype(str).str.strip()
    raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
    raw = raw[raw["close"].notna() & (raw["close"] > 0)]
    if raw.empty:
        return pd.DataFrame()

    adj2 = adj.copy()
    adj2["date"] = adj2["date"].astype(str)
    out = apply_hfq(raw, adj2)
    if out.empty:
        return pd.DataFrame()
    out = out.sort_values(["code", "date"]).reset_index(drop=True)
    out["hfq_pct"] = out.groupby("code", sort=False)["close"].pct_change() * 100.0
    out["prev_date"] = out.groupby("code", sort=False)["date"].shift(1)

    raw_pct = (
        raw.sort_values(["code", "date"])
        .assign(raw_pct=lambda x: x.groupby("code", sort=False)["close"].pct_change() * 100.0)
        [["code", "date", "close", "raw_pct"]]
        .rename(columns={"close": "raw_close"})
    )
    m = out.merge(raw_pct, on=["code", "date"], how="left")

    if cal:
        nxt = _calendar_next_map(cal)
        adjacent = m["prev_date"].map(nxt) == m["date"]
    else:
        # 无日历时退化为「库内相邻」；仍用 raw/hfq 对比过滤次新大波动
        adjacent = m["prev_date"].notna()

    bad = m[adjacent & (m["hfq_pct"].abs() > jump_pct) & (m["raw_pct"].abs() <= jump_pct)]
    return bad.reset_index(drop=True)


def _check_adj_jumps(daily: pd.DataFrame, cal: list[str] | None = None) -> None:
    """后复权跳空检测——只抓复权/因子错接，不误伤停牌复牌与次新波动。"""
    from quant.data.store import read_adj_factor

    adj = read_adj_factor()
    if adj.empty:
        _note("FAIL", "adj_factor 为空 → load_adjusted_daily 退化为未复权价，除权日必假跳空（须先补 adj_factor）")
        return
    bad = adj_jump_suspects(daily, adj, cal)
    if bad.empty:
        print(
            f"  [OK]   后复权异常跳空 >{JUMP_PCT}%: 0 个"
            f"（已豁免非相邻交易日 / 原料同步大波动）"
        )
    else:
        samples = bad[["code", "date", "close", "hfq_pct", "raw_pct"]].head(5).to_dict("records")
        _note(
            "FAIL",
            f"后复权异常跳空 >{JUMP_PCT}%（原料未同步）: {len(bad)} 个（复权/数据 bug）样例: {samples}",
        )


def _check_price_sanity(daily: pd.DataFrame) -> None:
    d = daily.copy()
    for c in ("open", "high", "low", "close", "volume", "amount"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    n = len(d)
    bad_close = (d["close"].fillna(0) <= 0).sum()
    bad_hl = (d["high"] < d["low"]).sum()
    bad_high = (d["high"] < d[["open", "close"]].max(axis=1)).sum()
    bad_low = (d["low"] > d[["open", "close"]].min(axis=1)).sum()
    bad_neg = ((d["volume"] < 0) | (d["amount"] < 0)).sum()
    total = bad_close + bad_hl + bad_high + bad_low + bad_neg
    if total == 0:
        print("  [OK]   价格 sanity: close>0 / high>=low / high·low 夹住 open·close / volume·amount>=0 全过")
    else:
        _note(
            "FAIL",
            f"价格 sanity 违规 {total}/{n} 行 "
            f"(close<=0 {bad_close}, high<low {bad_hl}, high 不夹 {bad_high}, low 不夹 {bad_low}, 负量 {bad_neg})",
        )


def _check_calendar_coverage(daily: pd.DataFrame, cal: list[str], start: str, end: str) -> None:
    if not cal:
        _note("FAIL", "calendar 为空")
        return
    days = {d for d in cal if start <= d <= end}
    if not days:
        _note("FAIL", f"日历在 [{start},{end}] 内无交易日")
        return
    # 市场级缺日只查库的实际日期跨度内（库外 [min,max] 之前/之后无数据属正常）
    lo_lib, hi_lib = daily["date"].min(), daily["date"].max()
    span = {d for d in days if lo_lib <= d <= hi_lib}
    have = {str(d) for d in daily["date"].astype(str).unique()}
    market_missing = sorted(span - have)
    if market_missing:
        _note("WARN", f"市场级缺日 {len(market_missing)} 个: {market_missing[:5]}...（全市场无数据，多为漏拉/停市）")
    # per-code 覆盖：期望 = 该码在库内首末日期夹的日历日 − 无行情豁免
    from scripts.data.build_daily import load_no_bar_map

    try:
        no_bar = load_no_bar_map()
    except Exception:  # noqa: BLE001
        no_bar = {}
    per_code: list[tuple[str, float]] = []
    for code, g in daily.groupby(daily["code"].astype(str).str.strip()):
        ds = {str(x) for x in g["date"].astype(str)}
        lo, hi = min(ds), max(ds)
        exp = {d for d in days if lo <= d <= hi} - no_bar.get(code, set())
        if not exp:
            continue
        cov = len(ds & exp) / len(exp)
        per_code.append((code, cov))
    if not per_code:
        return
    covs = np.array([c for _, c in per_code])
    low = [f"{c}({x:.0%})" for c, x in sorted(per_code, key=lambda t: t[1])[:5]]
    print(
        f"  [OK]   per-code 日历覆盖: 均值 {covs.mean():.1%} · 最低 {covs.min():.0%} "
        f"({len([c for c in covs if c < CAL_COVER_MIN])} 码 <{CAL_COVER_MIN:.0%})"
    )
    if len([c for c in covs if c < CAL_COVER_MIN]) > 0:
        _note("WARN", f"覆盖最低 5 码: {low}（次新/停牌为合法缺日，可查）")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=None, help="quant-home 根；默认当前 QUANT_HOME")
    ap.add_argument("--start", default="2000-01-01", help="检查起始日（默认全区间）")
    ap.add_argument("--end", default="2099-12-31", help="检查结束日（默认全区间）")
    args = ap.parse_args()

    home = Path(args.home).expanduser() if args.home else None
    log_progress_start(_SCOPE, "开始", detail=f"home={home or '当前'} [{args.start},{args.end}]")
    # 全部检查（含 read_index_daily / read_adj_factor / load_no_bar_map）都须在
    # override 上下文内，否则 --home 只作用于前三读、其余读错目录。
    ctx = override_quant_home(home) if home else __import__("contextlib").nullcontext()
    try:
        with ctx:
            daily = read_daily_raw()
            adj = read_adj_factor()
            cal = read_calendar()

            print(f"=== 校验 home: {home or '当前'}  窗口 [{args.start}, {args.end}] ===\n")

            # [1] 基础
            print("[1] 基础")
            if daily.empty:
                _note("FAIL", "daily_raw 为空")
            else:
                n_code = daily["code"].astype(str).nunique()
                if "name" in daily.columns:
                    name_fill = daily["name"].fillna("").astype(str).str.strip().ne("").mean()
                else:
                    name_fill = 0.0  # build_daily 的 stock_zh_a_hist 行无 name 列
                print(f"  [OK]   daily_raw: {len(daily):,} 行 / {n_code} 码 / "
                      f"{daily['date'].min()} ~ {daily['date'].max()}")
                print(f"  [INF]  name 非空率 {name_fill:.1%}（ST 过滤健康度；低则依赖 name_snapshot）")
            print(f"  [INF]  calendar {len(cal)} 天 · adj_factor {0 if adj is None or adj.empty else len(adj)} 行")

            # [2] 完整性
            print("\n[2] 完整性")
            if not daily.empty and adj is not None and not adj.empty:
                adj_codes = set(adj["code"].astype(str))
                raw_codes = set(daily["code"].astype(str))
                cov = len(adj_codes & raw_codes) / max(len(raw_codes), 1)
                if cov >= ADJ_COVER_MIN:
                    print(f"  [OK]   adj_factor 覆盖 {cov:.1%} ({len(adj_codes & raw_codes)}/{len(raw_codes)})")
                else:
                    _note("FAIL", f"adj_factor 覆盖 {cov:.1%} <{ADJ_COVER_MIN:.0%}（须先补复权因子，否则除权假跳空）")
            elif daily.empty:
                pass
            else:
                _note("FAIL", "adj_factor 为空（须先跑 build_daily 的 _refresh_adj_all 或 refresh_adj_for_codes）")
            # 历史段缺列覆盖（build_daily 的 stock_zh_a_hist 行缺；backfill_daily_meta 补）
            for c, label, min_ok in (
                ("float_mv", "市值(float_mv)覆盖", 0.5),
                ("pre_close", "pre_close 覆盖", 0.9),
            ):
                if c in daily.columns:
                    cov = daily[c].notna().mean()
                    if cov >= min_ok:
                        print(f"  [OK]   {label} {cov:.1%}")
                    else:
                        _note("WARN", f"{label} {cov:.1%} <{min_ok:.0%}（历史段缺列，可跑 backfill_daily_meta）")
                else:
                    _note("WARN", f"{label} 列缺失（可跑 backfill_daily_meta 补）")
            _check_index()
            if not daily.empty and cal:
                _check_calendar_coverage(daily, cal, args.start, args.end)

            # [3] 正确性
            print("\n[3] 正确性")
            if daily.empty:
                pass
            else:
                dup = daily.duplicated(subset=["code", "date"]).sum()
                if dup == 0:
                    print("  [OK]   重复 (code,date): 0")
                else:
                    _note("FAIL", f"重复 (code,date): {dup}")
                _check_price_sanity(daily)
                _check_adj_jumps(daily, cal)

        # 汇总
        print("\n=== 结果 ===")
        if FAILS:
            print(f"发现问题: {len(FAILS)} 项 FAIL（需处理）+ {len(WARNS)} 项 WARN（可查）")
            for f in FAILS:
                print(f"  FAIL  {f}")
            log_progress_error(_SCOPE, "失败", detail=f"{len(FAILS)} FAIL + {len(WARNS)} WARN")
            sys.exit(1)
        print(f"通过 OK（{len(WARNS)} 项 WARN 提示，可查可不查）")
        for w in WARNS:
            print(f"  WARN  {w}")
        log_progress_done(_SCOPE, "成功", detail=f"{len(WARNS)} WARN")
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
