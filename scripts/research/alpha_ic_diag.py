"""合成 alpha 分年 RankIC / 多空价差诊断（不依赖 panel parquet）。

用法::

    poetry run python -m scripts.research.alpha_ic_diag \\
        --home D:/ProgramData/.quant \\
        --alpha-cache D:/ProgramData/.quant/reports/bt_profit_calm/alpha_by_date.pkl \\
        --start 2023-01-01 --end 2025-12-31 --horizon 5
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso
from scripts.cli_home import add_home_argument, home_context

_SCOPE = "alpha_ic_diag"


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 5:
        return float("nan")
    ra = pd.Series(a).rank().to_numpy()
    rb = pd.Series(b).rank().to_numpy()
    if ra.std() < 1e-12 or rb.std() < 1e-12:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def _fwd_map(daily: pd.DataFrame, horizon: int) -> dict[tuple[str, str], float]:
    """(code, date) → 未来 horizon 日收益（用后复权 close）。"""
    d = daily.copy()
    if not pd.api.types.is_string_dtype(d["date"]):
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    else:
        d["date"] = d["date"].astype(str).str[:10]
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    out: dict[tuple[str, str], float] = {}
    for code, g in d.groupby("code", sort=False):
        g = g.sort_values("date")
        closes = g["close"].to_numpy(dtype=float)
        dates = g["date"].tolist()
        for i in range(len(dates) - horizon):
            c0, c1 = closes[i], closes[i + horizon]
            if c0 > 0 and c1 == c1 and c0 == c0:
                out[(str(code), dates[i])] = c1 / c0 - 1.0
    return out


def diagnose(alpha_by_date: dict, fwd: dict[tuple[str, str], float], *, horizon: int) -> dict:
    daily_ics: list[tuple[str, float, int]] = []
    top_bot: list[tuple[str, float]] = []
    for dt, alpha in sorted(alpha_by_date.items()):
        xs: list[float] = []
        ys: list[float] = []
        for code, a in alpha.items():
            fr = fwd.get((str(code), dt[:10]))
            if fr is None or not np.isfinite(a) or not np.isfinite(fr):
                continue
            xs.append(float(a))
            ys.append(float(fr))
        if len(xs) < 30:
            continue
        ic = _spearman(np.array(xs), np.array(ys))
        if ic == ic:
            daily_ics.append((dt[:10], ic, len(xs)))
        # top/bottom 20%
        order = np.argsort(xs)
        n = len(xs)
        k = max(1, n // 5)
        bot = np.mean([ys[i] for i in order[:k]])
        top = np.mean([ys[i] for i in order[-k:]])
        top_bot.append((dt[:10], float(top - bot)))

    if not daily_ics:
        return {"error": "no overlapping alpha/fwd samples", "n_days": 0}

    ics = np.array([x[1] for x in daily_ics])
    spreads = np.array([x[1] for x in top_bot])
    by_year: dict[str, dict] = {}
    year_ics: dict[str, list[float]] = defaultdict(list)
    year_sp: dict[str, list[float]] = defaultdict(list)
    for dt, ic, _n in daily_ics:
        year_ics[dt[:4]].append(ic)
    for dt, sp in top_bot:
        year_sp[dt[:4]].append(sp)
    for y in sorted(year_ics):
        arr = np.array(year_ics[y])
        sp = np.array(year_sp.get(y, []))
        t = float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr)))) if len(arr) > 2 and arr.std(ddof=1) > 1e-12 else 0.0
        by_year[y] = {
            "n_days": int(len(arr)),
            "ic_mean": round(float(arr.mean()), 4),
            "ic_std": round(float(arr.std(ddof=1)), 4),
            "ic_t": round(t, 2),
            "ic_hit": round(float((arr > 0).mean()), 3),
            "ls_spread_bps": round(float(sp.mean() * 1e4), 1) if len(sp) else None,
        }

    t_all = float(ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))) if len(ics) > 2 and ics.std(ddof=1) > 1e-12 else 0.0
    return {
        "horizon": horizon,
        "n_days": int(len(ics)),
        "ic_mean": round(float(ics.mean()), 4),
        "ic_std": round(float(ics.std(ddof=1)), 4),
        "ic_t": round(t_all, 2),
        "ic_hit": round(float((ics > 0).mean()), 3),
        "ls_spread_bps": round(float(spreads.mean() * 1e4), 1),
        "by_year": by_year,
        "verdict": (
            "POSITIVE" if t_all >= 2 and float(ics.mean()) > 0
            else "WEAK_POS" if float(ics.mean()) > 0 and t_all >= 1
            else "FLAT" if abs(float(ics.mean())) < 0.005
            else "NEGATIVE"
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="合成 alpha 分年 RankIC 诊断")
    add_home_argument(ap)
    ap.add_argument("--alpha-cache", required=True, help="alpha_by_date.pkl")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    log_progress_start(_SCOPE, "开始", detail=args.alpha_cache)
    try:
        with home_context(args.home):
            cache = Path(args.alpha_cache)
            if not cache.is_file():
                log_progress_error(_SCOPE, "失败", detail=f"无缓存 {cache}")
                sys.exit(1)
            with cache.open("rb") as f:
                alpha_by_date = pickle.load(f)
            if args.start or args.end:
                lo = (args.start or "0000")[:10]
                hi = (args.end or "9999")[:10]
                alpha_by_date = {
                    k: v
                    for k, v in alpha_by_date.items()
                    if lo <= str(k)[:10] <= hi
                }
            log_progress(_SCOPE, "加载 daily / 算前瞻收益", detail=f"h={args.horizon}")
            daily = load_adjusted_daily()
            fwd = _fwd_map(daily, args.horizon)
            rep = diagnose(alpha_by_date, fwd, horizon=args.horizon)
            out = Path(args.out or (cache.parent / f"alpha_ic_h{args.horizon}.json"))
            out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(rep, ensure_ascii=False, indent=2), flush=True)
            log_progress_done(_SCOPE, "成功", detail=f"{out} verdict={rep.get('verdict')}")
    except SystemExit:
        raise
    except Exception as e:
        log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
