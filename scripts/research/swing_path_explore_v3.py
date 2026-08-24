"""波段探索 v3：市场宽度作 regime + 周线多头内选偏弱（过程对齐 + 数据偏置）。

v2：强势回踩为负，弱势回踩微正；库内无指数日线，regime 需改宽度。
本轮：
  M1  市场宽度（>MA50 占比≥55%）时随机
  M2  宽度差时随机
  M3  周线多头 ∩ 截面 ret60 ≤ p40（偏弱回踩）
  M4  周线多头 ∩ 截面 ret60 ≥ p70（强势回踩，对照）
  M5  周线多头 ∩ 宽度好 ∩ 偏弱回踩
  M6  仅流动性随机基线
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from quant.data.adjust import load_adjusted_daily
from quant.data.universe import _is_st_name
from quant.exit.atr import atr
from scripts.cli_home import add_home_argument, home_context


@dataclass
class PathStats:
    path: str
    n: int
    fwd5_mean: float
    fwd5_win: float
    fwd10_mean: float
    fwd10_win: float
    fwd20_mean: float
    fwd20_win: float
    notes: str = ""


def _fwd(rets: list[float]) -> tuple[float, float]:
    if not rets:
        return float("nan"), float("nan")
    a = np.asarray(rets, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return float("nan"), float("nan")
    return float(a.mean() * 100), float((a > 0).mean() * 100)


def _pack(path: str, buckets: dict[int, list[float]], notes: str = "") -> PathStats:
    m5, w5 = _fwd(buckets[5])
    m10, w10 = _fwd(buckets[10])
    m20, w20 = _fwd(buckets[20])
    return PathStats(
        path=path,
        n=len(buckets[5]),
        fwd5_mean=round(m5, 3),
        fwd5_win=round(w5, 1),
        fwd10_mean=round(m10, 3),
        fwd10_win=round(w10, 1),
        fwd20_mean=round(m20, 3),
        fwd20_win=round(w20, 1),
        notes=notes,
    )


def explore(daily: pd.DataFrame, start: str, end: str, min_adv: float) -> dict:
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d["code"] = d["code"].astype(str)
    if "name" not in d.columns:
        d["name"] = ""
    if "amount" not in d.columns:
        d["amount"] = 0.0

    groups = list(d.groupby("code", sort=False))
    n_codes = len(groups)

    # Pass1: 每日 ret60 列表 + above_ma50 计数 → 宽度 & 分位
    print("pass1…", flush=True)
    day_ret60: dict[str, list[float]] = defaultdict(list)
    day_above: dict[str, list[int]] = defaultdict(list)

    for gi, (code, g) in enumerate(groups):
        if (gi + 1) % 800 == 0:
            print(f"  pass1 {gi+1}/{n_codes}", flush=True)
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < 120:
            continue
        if _is_st_name(str(g["name"].iloc[-1] if "name" in g.columns else "")):
            continue
        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        amount = pd.to_numeric(g["amount"], errors="coerce").to_numpy(dtype=float)
        adv = pd.Series(amount).rolling(20, min_periods=10).mean().to_numpy()
        ma50 = pd.Series(close).rolling(50).mean().to_numpy()
        dates = g["date"].astype(str).to_numpy()
        for i in range(60, len(g)):
            dt = dates[i]
            if dt < start or dt > end:
                continue
            if not np.isfinite(adv[i]) or float(adv[i]) < min_adv:
                continue
            if close[i] <= 0 or close[i - 60] <= 0 or not np.isfinite(ma50[i]):
                continue
            day_ret60[dt].append(float(close[i] / close[i - 60] - 1.0))
            day_above[dt].append(1 if close[i] > ma50[i] else 0)

    breadth = {
        dt: float(np.mean(vs)) for dt, vs in day_above.items() if len(vs) >= 80
    }
    p40 = {dt: float(np.quantile(vs, 0.40)) for dt, vs in day_ret60.items() if len(vs) >= 80}
    p70 = {dt: float(np.quantile(vs, 0.70)) for dt, vs in day_ret60.items() if len(vs) >= 80}
    print(f"breadth days={len(breadth)}", flush=True)

    paths = {k: {5: [], 10: [], 20: []} for k in (
        "M6_random",
        "M1_breadth_good_rand",
        "M2_breadth_bad_rand",
        "M3_w_up_weak_pb",
        "M4_w_up_strong_pb",
        "M5_breadth_w_weak_pb",
    )}
    rng = np.random.default_rng(11)
    t0 = time.perf_counter()
    print("pass2…", flush=True)

    for gi, (code, g) in enumerate(groups):
        if (gi + 1) % 500 == 0:
            print(f"  pass2 {gi+1}/{n_codes}", flush=True)
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < 200:
            continue
        if _is_st_name(str(g["name"].iloc[-1] if "name" in g.columns else "")):
            continue

        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        high = pd.to_numeric(g["high"], errors="coerce").to_numpy(dtype=float)
        low = pd.to_numeric(g["low"], errors="coerce").to_numpy(dtype=float)
        amount = pd.to_numeric(g["amount"], errors="coerce").to_numpy(dtype=float)
        dates = g["date"].astype(str).to_numpy()
        ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().to_numpy()
        adv = pd.Series(amount).rolling(20, min_periods=10).mean().to_numpy()
        a14 = atr(g, 14).to_numpy(dtype=float)

        # weekly
        g2 = g.copy()
        g2["_dt"] = pd.to_datetime(g2["date"])
        w = (
            g2.set_index("_dt")
            .resample("W-FRI")
            .agg({"close": "last"})
            .dropna()
        )
        if len(w) < 40:
            continue
        wc = w["close"].to_numpy(dtype=float)
        wma10 = pd.Series(wc).rolling(10).mean().to_numpy()
        wma20 = pd.Series(wc).rolling(20).mean().to_numpy()
        w_up_map = {}
        for i in range(len(w)):
            if not np.isfinite(wma10[i]) or not np.isfinite(wma20[i]):
                continue
            w_up_map[w.index[i].strftime("%Y-%m-%d")] = bool(
                wc[i] > wma10[i] > wma20[i] and wma10[i] >= wma10[max(0, i - 2)]
            )
        w_dates = sorted(w_up_map)
        wi = 0
        last_w = False
        week_flag = np.zeros(len(g), dtype=bool)
        for i, dt in enumerate(dates):
            while wi < len(w_dates) and w_dates[wi] <= dt:
                last_w = w_up_map[w_dates[wi]]
                wi += 1
            week_flag[i] = last_w

        for i in range(80, len(g) - 21):
            dt = dates[i]
            if dt < start or dt > end:
                continue
            c0 = float(close[i])
            if c0 <= 0 or not np.isfinite(c0):
                continue
            if not np.isfinite(adv[i]) or float(adv[i]) < min_adv:
                continue
            a = float(a14[i])
            if not np.isfinite(a) or a <= 0 or close[i - 60] <= 0:
                continue
            if dt not in p40 or dt not in breadth:
                continue

            ret60 = float(c0 / close[i - 60] - 1.0)
            br = breadth[dt]
            good = br >= 0.55
            bad = br <= 0.40
            weak = ret60 <= p40[dt]
            strong = ret60 >= p70[dt]
            peak20 = float(np.nanmax(high[i - 19 : i + 1]))
            pullback = (peak20 - c0) / a
            touched = float(np.nanmin(low[i - 2 : i + 1])) <= float(ema20[i]) * 1.01
            hold = c0 > float(ema20[i]) and c0 > float(close[i - 1])
            pb = touched and hold and 0.5 <= pullback <= 3.0
            w_ok = bool(week_flag[i])

            def add(name: str, ok: bool) -> None:
                if not ok:
                    return
                for n in (5, 10, 20):
                    paths[name][n].append(float(close[i + n]) / c0 - 1.0)

            if rng.random() < 0.012:
                add("M6_random", True)
            if good and rng.random() < 0.025:
                add("M1_breadth_good_rand", True)
            if bad and rng.random() < 0.025:
                add("M2_breadth_bad_rand", True)

            add("M3_w_up_weak_pb", w_ok and weak and pb)
            add("M4_w_up_strong_pb", w_ok and strong and pb)
            add("M5_breadth_w_weak_pb", good and w_ok and weak and pb)

    print(f"pass2 {time.perf_counter()-t0:.1f}s", flush=True)
    notes = {
        "M6_random": "流动性随机基线",
        "M1_breadth_good_rand": "宽度≥55%时随机",
        "M2_breadth_bad_rand": "宽度≤40%时随机",
        "M3_w_up_weak_pb": "周线多头∩截面偏弱∩回踩企稳",
        "M4_w_up_strong_pb": "周线多头∩截面强势∩回踩企稳",
        "M5_breadth_w_weak_pb": "宽度好∩周线多头∩偏弱回踩",
    }
    stats = [_pack(k, v, notes[k]) for k, v in paths.items()]
    stats.sort(key=lambda x: (-(x.fwd10_mean if np.isfinite(x.fwd10_mean) else -999), -x.n))
    return {
        "paths": [asdict(s) for s in stats],
        "breadth_note": "breadth=当日流动性池中收盘>MA50占比",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--min-adv", type=float, default=1.5e8)
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home

        print("load…", flush=True)
        daily = load_adjusted_daily()
        res = explore(daily, args.start, args.end, args.min_adv)
        out = Path(quant_home()) / "reports/bt_stage_swing"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"swing_path_explore_v3_{args.start}_{args.end}.json"
        payload = {"start": args.start, "end": args.end, "min_adv": args.min_adv, **res}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print("\n=== PATH RANK fwd10 ===", flush=True)
        for s in res["paths"]:
            print(
                f"{s['path']:24s} n={s['n']:6d} "
                f"5d={s['fwd5_mean']:+6.2f}%/{s['fwd5_win']:4.1f}% "
                f"10d={s['fwd10_mean']:+6.2f}%/{s['fwd10_win']:4.1f}% "
                f"20d={s['fwd20_mean']:+6.2f}%/{s['fwd20_win']:4.1f}% | {s['notes']}",
                flush=True,
            )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
