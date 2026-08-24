"""波段探索 v2：市场状态 / 相对强度 / 突破后首回踩 / 赢家特征 lift。

v1 结论：A/B/C/D 经典路径 2021–2025 全员低于流动性随机基线。
本轮问：
  1) 仅在指数多头时做，是否翻正？
  2) 只做相对强（60日收益全市场分位高）的回踩，是否更好？
  3) 「突破后第一次回踩」是否优于任意回踩？
  4) 事后大涨（20日≥+10%）的入场日，哪些特征 lift 最高？
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


INDEX_CANDIDATES = ("000300.SH", "399300.SZ", "000001.SH", "399001.SZ")


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


def _pick_index(daily: pd.DataFrame) -> pd.DataFrame | None:
    codes = set(daily["code"].astype(str).unique())
    for c in INDEX_CANDIDATES:
        if c in codes:
            g = daily[daily["code"].astype(str) == c].sort_values("date")
            if len(g) > 300:
                print(f"index proxy: {c}", flush=True)
                return g.reset_index(drop=True)
    # 合成：每日等权中位数收益累积（慢）；退而用全市场中位收盘变化
    print("index proxy: cross-section median close (synthetic)", flush=True)
    return None


def explore(daily: pd.DataFrame, start: str, end: str, min_adv: float) -> dict:
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d["code"] = d["code"].astype(str)
    if "name" not in d.columns:
        d["name"] = ""
    if "amount" not in d.columns:
        d["amount"] = 0.0

    idx = _pick_index(d)
    idx_up: dict[str, bool] = {}
    if idx is not None:
        ic = pd.to_numeric(idx["close"], errors="coerce")
        ima = ic.rolling(60).mean()
        for i in range(len(idx)):
            dt = str(idx.at[i, "date"])[:10]
            c = float(ic.iloc[i])
            m = float(ima.iloc[i]) if np.isfinite(ima.iloc[i]) else np.nan
            idx_up[dt] = bool(np.isfinite(m) and c > m)

    # 预计算每日截面 60 日收益分位需要：先扫一遍算 ret60
    print("pass1: per-code features…", flush=True)
    # 存每个 (date, code) 的候选特征太重；改为两阶段：
    # 1) 收集每日所有合格票的 ret60 分布 → 分位阈值
    # 2) 再扫信号

    # 为节省内存：按日存 ret60 列表算分位数阈值，再第二遍打标
    day_ret60: dict[str, list[float]] = defaultdict(list)
    groups = list(d.groupby("code", sort=False))
    n_codes = len(groups)

    # Pass1: build day_ret60 for liquid names
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
        dates = g["date"].astype(str).to_numpy()
        for i in range(60, len(g)):
            if dates[i] < start or dates[i] > end:
                continue
            if not np.isfinite(adv[i]) or float(adv[i]) < min_adv:
                continue
            if close[i - 60] <= 0 or not np.isfinite(close[i]) or close[i] <= 0:
                continue
            day_ret60[dates[i]].append(float(close[i] / close[i - 60] - 1.0))

    # 分位阈值：每日 p70
    day_p70 = {
        dt: float(np.quantile(vs, 0.70)) for dt, vs in day_ret60.items() if len(vs) >= 50
    }
    print(f"pass1 days with p70={len(day_p70)}", flush=True)

    paths = {
        "E_random": {5: [], 10: [], 20: []},
        "F_regime_only": {5: [], 10: [], 20: []},  # 指数多头下随机
        "G_rs70_pb": {5: [], 10: [], 20: []},  # 强势股回踩
        "H_regime_rs_pb": {5: [], 10: [], 20: []},
        "I_breakout_first_pb": {5: [], 10: [], 20: []},
        "J_regime_breakout_pb": {5: [], 10: [], 20: []},
        "K_weak_rs30_pb": {5: [], 10: [], 20: []},  # 对照：弱势回踩（反转）
    }

    # 赢家特征：fwd20>=10% vs fwd20<=-5%
    feat_names = [
        "ret60",
        "pullback_atr",
        "vol_ratio_pb",
        "above_ma50",
        "rsi14",
        "idx_up",
        "dist_ma20",
    ]
    win_feats: dict[str, list[float]] = defaultdict(list)
    lose_feats: dict[str, list[float]] = defaultdict(list)

    rng = np.random.default_rng(7)
    t0 = time.perf_counter()
    print("pass2: signals…", flush=True)

    for gi, (code, g) in enumerate(groups):
        if (gi + 1) % 500 == 0:
            print(f"  pass2 {gi+1}/{n_codes}", flush=True)
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < 160:
            continue
        if _is_st_name(str(g["name"].iloc[-1] if "name" in g.columns else "")):
            continue

        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        high = pd.to_numeric(g["high"], errors="coerce").to_numpy(dtype=float)
        low = pd.to_numeric(g["low"], errors="coerce").to_numpy(dtype=float)
        amount = pd.to_numeric(g["amount"], errors="coerce").to_numpy(dtype=float)
        dates = g["date"].astype(str).to_numpy()
        cs = pd.Series(close)
        ma20 = cs.rolling(20).mean().to_numpy()
        ma50 = cs.rolling(50).mean().to_numpy()
        ema20 = cs.ewm(span=20, adjust=False).mean().to_numpy()
        adv = pd.Series(amount).rolling(20, min_periods=10).mean().to_numpy()
        a14 = atr(g, 14).to_numpy(dtype=float)
        # RSI
        diff = cs.diff()
        up = diff.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        dn = (-diff).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy(dtype=float)

        # 60日高点位置：用于「突破后首回踩」
        hi60 = pd.Series(high).rolling(60).max().to_numpy()
        # 是否在过去 15 日内创新高（突破），且当前是之后首次回踩到 ema20
        made_high = np.zeros(len(g), dtype=bool)
        for i in range(60, len(g)):
            made_high[i] = bool(np.isfinite(hi60[i]) and high[i] >= hi60[i] * 0.999)

        last_break_i = -999
        for i in range(80, len(g) - 21):
            dt = dates[i]
            if dt < start or dt > end:
                continue
            c0 = float(close[i])
            if not np.isfinite(c0) or c0 <= 0:
                continue
            if not np.isfinite(adv[i]) or float(adv[i]) < min_adv:
                continue
            if made_high[i]:
                last_break_i = i

            a = float(a14[i])
            if not np.isfinite(a) or a <= 0:
                continue
            if close[i - 60] <= 0:
                continue
            ret60 = float(c0 / close[i - 60] - 1.0)
            p70 = day_p70.get(dt)
            if p70 is None:
                continue
            rs_strong = ret60 >= p70
            # 弱势：低于 p30
            # 用当日列表太重，近似：ret60 < 0 且不在强势
            rs_weak = ret60 < 0 and not rs_strong

            peak20 = float(np.nanmax(high[i - 19 : i + 1]))
            pullback = (peak20 - c0) / a
            touched = float(np.nanmin(low[i - 2 : i + 1])) <= float(ema20[i]) * 1.01
            hold = c0 > float(ema20[i]) and c0 > float(close[i - 1])
            pb_ok = touched and hold and 0.5 <= pullback <= 3.0
            regime = bool(idx_up.get(dt, False)) if idx_up else (float(ma50[i]) > 0 and c0 > float(ma50[i]))

            amt_impulse = float(np.nanmean(amount[i - 14 : i - 5])) if i >= 15 else np.nan
            amt_pb = float(np.nanmean(amount[i - 5 : i])) if i >= 6 else np.nan
            vol_ratio = (amt_pb / amt_impulse) if (np.isfinite(amt_pb) and amt_impulse > 0) else np.nan

            def add(name: str, ok: bool) -> None:
                if not ok:
                    return
                for n in (5, 10, 20):
                    paths[name][n].append(float(close[i + n]) / c0 - 1.0)

            if rng.random() < 0.015:
                add("E_random", True)
            if regime and rng.random() < 0.03:
                add("F_regime_only", True)

            add("G_rs70_pb", rs_strong and pb_ok)
            add("H_regime_rs_pb", regime and rs_strong and pb_ok)
            add("K_weak_rs30_pb", rs_weak and pb_ok)

            # 突破后 3–12 日内首次回踩
            gap = i - last_break_i
            first_pb = pb_ok and 3 <= gap <= 12
            add("I_breakout_first_pb", first_pb)
            add("J_regime_breakout_pb", regime and first_pb and rs_strong)

            # 特征库：流动性池抽样
            if rng.random() < 0.08:
                fwd20 = float(close[i + 20]) / c0 - 1.0
                feats = {
                    "ret60": ret60,
                    "pullback_atr": pullback,
                    "vol_ratio_pb": vol_ratio if np.isfinite(vol_ratio) else np.nan,
                    "above_ma50": 1.0 if c0 > float(ma50[i]) else 0.0,
                    "rsi14": float(rsi[i]) if np.isfinite(rsi[i]) else np.nan,
                    "idx_up": 1.0 if regime else 0.0,
                    "dist_ma20": c0 / float(ma20[i]) - 1.0 if np.isfinite(ma20[i]) and ma20[i] > 0 else np.nan,
                }
                if fwd20 >= 0.10:
                    for k, v in feats.items():
                        if np.isfinite(v):
                            win_feats[k].append(float(v))
                elif fwd20 <= -0.05:
                    for k, v in feats.items():
                        if np.isfinite(v):
                            lose_feats[k].append(float(v))

    print(f"pass2 done {time.perf_counter()-t0:.1f}s", flush=True)

    notes = {
        "E_random": "流动性池随机",
        "F_regime_only": "指数>MA60 时随机",
        "G_rs70_pb": "60日收益≥当日p70 + EMA20回踩企稳",
        "H_regime_rs_pb": "指数多头 ∩ 强势回踩",
        "I_breakout_first_pb": "60日新高后3-12日首次回踩",
        "J_regime_breakout_pb": "指数多头 ∩ 强势 ∩ 突破后首回踩",
        "K_weak_rs30_pb": "对照：弱势股回踩（偏反转）",
    }
    stats = [_pack(k, v, notes[k]) for k, v in paths.items()]
    stats.sort(key=lambda x: (-(x.fwd10_mean if np.isfinite(x.fwd10_mean) else -999), -x.n))

    lifts = []
    for k in feat_names:
        w = np.asarray(win_feats.get(k, []), dtype=float)
        l = np.asarray(lose_feats.get(k, []), dtype=float)
        if len(w) < 50 or len(l) < 50:
            continue
        lifts.append(
            {
                "feature": k,
                "win_mean": round(float(np.mean(w)), 4),
                "lose_mean": round(float(np.mean(l)), 4),
                "lift_win_minus_lose": round(float(np.mean(w) - np.mean(l)), 4),
                "n_win": int(len(w)),
                "n_lose": int(len(l)),
            }
        )
    lifts.sort(key=lambda x: -abs(x["lift_win_minus_lose"]))

    return {"paths": [asdict(s) for s in stats], "feature_lift": lifts}


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
        print(
            f"rows={len(daily)} codes={daily['code'].nunique()} "
            f"{daily['date'].min()}~{daily['date'].max()}",
            flush=True,
        )
        res = explore(daily, args.start, args.end, args.min_adv)
        out = Path(quant_home()) / "reports/bt_stage_swing"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"swing_path_explore_v2_{args.start}_{args.end}.json"
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
        print("\n=== FEATURE LIFT (fwd20≥10% vs ≤-5%) ===", flush=True)
        for x in res["feature_lift"]:
            print(
                f"{x['feature']:16s} win={x['win_mean']:+.4f} lose={x['lose_mean']:+.4f} "
                f"Δ={x['lift_win_minus_lose']:+.4f} (nW={x['n_win']} nL={x['n_lose']})",
                flush=True,
            )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
