"""离线库完整性与正确性校验。

跑一遍即知数据是否完整、是否正确（含复权连续性——除权尖刺 bug 探测器）。

用法：
    poetry run python -m scripts.data.validate_library
    poetry run python -m scripts.data.validate_library --home ~/.quant
    poetry run python -m scripts.data.validate_library --start 2021-01-01 --end 2026-08-03

退出码：0 = 通过；1 = 发现问题（完整性问题多为 FAIL；覆盖类低值记 WARN，因停牌/次新
属合法缺日）。

检查项：
[1] 基础      calendar 天数 / daily_raw 行·码·**实际起止** / name 非空率
[2] 完整性    adj_factor 覆盖率(<90% FAIL) / index_daily 000300·000905·000852(缺 FAIL) /
              市场级缺日（跨度内有数据交易日空洞，已排除非交易日）/ per-code 日历覆盖(低 WARN，
              已排除停牌等 no_bar 豁免日)
[3] 正确性    重复 (code,date)(>0 FAIL) / 价格 sanity(close>0、high>=low、high/low 夹住
              open·close、volume·amount>=0)(违规 FAIL) / 复权跳空：仅查日历相邻交易日，
              且「后复权跳 >28% 而原料价未跳」才 FAIL（停牌复牌/次新大波动豁免）
"""

from __future__ import annotations

import argparse
import sys

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
from scripts.cli_home import add_home_argument, home_context

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


def _fmt_dates(dates: list[str], *, per_line: int = 10) -> str:
    """缺日列表排版：每行最多 ``per_line`` 个，便于扫读。"""
    if not dates:
        return "（无）"
    if len(dates) <= per_line:
        return ", ".join(dates)
    lines = []
    for i in range(0, len(dates), per_line):
        lines.append(", ".join(dates[i : i + per_line]))
    return "\n           ".join(lines)


def data_span_and_market_gaps(
    daily: pd.DataFrame,
    cal: list[str],
    start: str,
    end: str,
    *,
    as_of: str | None = None,
) -> tuple[str | None, str | None, list[str], list[str], list[str]]:
    """窗口内实际数据起止、市场级缺日、末日之后未入库交易日。

    返回 ``(lo, hi, span_days, market_missing, after_end)``：
    - ``span_days``：``[lo, hi]`` 内日历交易日（已排除非交易日）
    - ``market_missing``：跨度内全市场都无 K 线的交易日（「中间缺」）
    - ``after_end``：数据末日之后、且不超过 ``as_of``（默认今天）的交易日
      （「尚未更新到」；**不含未来日**——日历常铺到远期，未来不可能有数据）
    """
    from datetime import date as date_cls

    from quant.data.calendar import to_iso

    if daily is None or daily.empty or not cal:
        return None, None, [], [], []
    d = daily.copy()
    d["date"] = d["date"].astype(str)
    in_win = d[(d["date"] >= start) & (d["date"] <= end)]
    if in_win.empty:
        return None, None, [], [], []
    lo = str(in_win["date"].min())
    hi = str(in_win["date"].max())
    days = sorted({str(x) for x in cal if start <= str(x) <= end})
    span = [x for x in days if lo <= x <= hi]
    have = set(in_win["date"].unique())
    market_missing = [x for x in span if x not in have]
    # 未入库只统计到「今天/as_of」：默认 end=2099 时日历含大量未来日，不应报缺
    cap = to_iso(as_of) if as_of else to_iso(date_cls.today())
    if end < cap:
        cap = end
    after_end = [x for x in days if hi < x <= cap]
    return lo, hi, span, market_missing, after_end


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

    lo_lib, hi_lib, span, market_missing, after_end = data_span_and_market_gaps(
        daily, cal, start, end
    )
    if lo_lib is None or hi_lib is None:
        _note("FAIL", f"窗口 [{start},{end}] 内 daily_raw 无数据")
        return

    n_have = len(span) - len(market_missing)
    print(f"  [INF]  实际数据起止: {lo_lib} ~ {hi_lib}")
    print(
        f"  [INF]  跨度内交易日 {len(span)} · 有数据日 {n_have} · 市场级缺日 {len(market_missing)}"
        f"（已排除非交易日；缺日=全市场当日无任何 K 线）"
    )
    if market_missing:
        _note(
            "WARN",
            f"市场级缺日 {len(market_missing)} 个（中间空洞，多为漏拉）:\n"
            f"           {_fmt_dates(market_missing)}",
        )
    else:
        print("  [OK]   市场级缺日: 0（跨度内每个交易日至少有一只票有 K 线）")
    if after_end:
        print(
            f"  [INF]  数据末日之后、截至今日仍有 {len(after_end)} 个交易日未入库"
            f"（尚未更新到；已排除未来日）:\n"
            f"           {_fmt_dates(after_end)}"
        )
    else:
        print(
            f"  [OK]   数据末日 {hi_lib} 之后至今日无可更新交易日"
            f"（未来日历日不计入未入库）"
        )

    # per-code 覆盖：期望 = 该码在库内首末日期夹的日历日 − 无行情豁免（停牌等）
    from scripts.data.build_daily import load_no_bar_map

    try:
        no_bar = load_no_bar_map()
    except Exception:  # noqa: BLE001
        no_bar = {}
    per_code: list[tuple[str, float, list[str]]] = []
    for code, g in daily.groupby(daily["code"].astype(str).str.strip()):
        ds = {str(x) for x in g["date"].astype(str)}
        lo, hi = min(ds), max(ds)
        exp = {d for d in days if lo <= d <= hi} - no_bar.get(code, set())
        if not exp:
            continue
        miss = sorted(exp - ds)
        cov = (len(exp) - len(miss)) / len(exp)
        per_code.append((code, cov, miss))
    if not per_code:
        return
    covs = np.array([c for _, c, _ in per_code])
    worst = sorted(per_code, key=lambda t: t[1])[:5]
    low = [f"{c}({x:.0%})" for c, x, _ in worst]
    print(
        f"  [OK]   per-code 日历覆盖: 均值 {covs.mean():.1%} · 最低 {covs.min():.0%} "
        f"({len([c for c in covs if c < CAL_COVER_MIN])} 码 <{CAL_COVER_MIN:.0%}；"
        f"已排除停牌等 no_bar 豁免日)"
    )
    if any(c < CAL_COVER_MIN for c in covs):
        samples = []
        for c, x, miss in worst:
            if not miss:
                continue
            show = ", ".join(miss[:8]) + ("…" if len(miss) > 8 else "")
            samples.append(f"{c} 缺{len(miss)}日[{show}]")
        detail = "；".join(samples) if samples else "（覆盖低但无未豁免缺日）"
        _note("WARN", f"覆盖最低 5 码: {low}；未豁免缺日样例: {detail}")


def main() -> None:
    ap = argparse.ArgumentParser(description="离线库完整性与正确性校验")
    add_home_argument(ap)
    ap.add_argument("--start", default="2000-01-01", help="检查起始日 YYYY-MM-DD（默认 2000-01-01，全区间）")
    ap.add_argument("--end", default="2099-12-31", help="检查结束日 YYYY-MM-DD（默认 2099-12-31，全区间）")
    args = ap.parse_args()

    log_progress_start(_SCOPE, "开始", detail=f"home={args.home or '当前'} [{args.start},{args.end}]")
    try:
        with home_context(args.home):
            daily = read_daily_raw()
            adj = read_adj_factor()
            cal = read_calendar()

            print(f"=== 校验 home: {args.home or '当前'}  窗口 [{args.start}, {args.end}] ===\n")

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
                lo_all = str(daily["date"].min())
                hi_all = str(daily["date"].max())
                lo_w, hi_w, _, _, _ = data_span_and_market_gaps(
                    daily, cal or [], args.start, args.end
                )
                print(
                    f"  [OK]   daily_raw: {len(daily):,} 行 / {n_code} 码 / "
                    f"全库起止 {lo_all} ~ {hi_all}"
                )
                if lo_w and hi_w and (lo_w != lo_all or hi_w != hi_all):
                    print(f"  [INF]  检查窗口内实际起止: {lo_w} ~ {hi_w}")
                elif lo_w and hi_w:
                    print(f"  [INF]  实际数据起止: {lo_w} ~ {hi_w}（窗口内）")
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
