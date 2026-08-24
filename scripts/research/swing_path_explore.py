"""波段规律探索：多路径事件研究（远期收益对照）。

路径（公开文献 + 本地数据可算）：
  A  周线趋势 + 日线回踩均线企稳
  B  量价：上涨段放量、回调缩量、确认日放量
  C  指标：RSI 冷却后拐头 / MACD 零轴上金叉附近
  D  对照：仅日线 MA60 上升回调（旧 stage 近似）
  E  对照：无过滤随机（同流动性池）

窗口默认 2021-01-01 ~ 2025-12-31；严格用 T 日收盘信号看 T+1..T+N 收益。
"""

from __future__ import annotations

import argparse
import json
import time
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
    fwd5_p50: float
    fwd5_win: float
    fwd10_mean: float
    fwd10_p50: float
    fwd10_win: float
    fwd20_mean: float
    fwd20_p50: float
    fwd20_win: float
    notes: str = ""


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    c = pd.Series(close)
    d = c.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    au = up.ewm(alpha=1 / period, adjust=False).mean()
    ad = dn.ewm(alpha=1 / period, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.to_numpy(dtype=float)


def _macd(close: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    c = pd.Series(close)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    return dif.to_numpy(dtype=float), dea.to_numpy(dtype=float)


def _fwd_stats(rets: list[float]) -> tuple[float, float, float]:
    if not rets:
        return float("nan"), float("nan"), float("nan")
    a = np.asarray(rets, dtype=float)
    a = a[np.isfinite(a)]
    if len(a) == 0:
        return float("nan"), float("nan"), float("nan")
    return float(a.mean() * 100), float(np.median(a) * 100), float((a > 0).mean() * 100)


def _pack(path: str, buckets: dict[int, list[float]], notes: str = "") -> PathStats:
    m5, p5, w5 = _fwd_stats(buckets[5])
    m10, p10, w10 = _fwd_stats(buckets[10])
    m20, p20, w20 = _fwd_stats(buckets[20])
    return PathStats(
        path=path,
        n=len(buckets[5]),
        fwd5_mean=round(m5, 3),
        fwd5_p50=round(p5, 3),
        fwd5_win=round(w5, 1),
        fwd10_mean=round(m10, 3),
        fwd10_p50=round(p10, 3),
        fwd10_win=round(w10, 1),
        fwd20_mean=round(m20, 3),
        fwd20_p50=round(p20, 3),
        fwd20_win=round(w20, 1),
        notes=notes,
    )


def explore(daily: pd.DataFrame, start: str, end: str, min_adv: float = 1.5e8) -> list[PathStats]:
    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d["code"] = d["code"].astype(str)
    if "name" not in d.columns:
        d["name"] = ""
    if "amount" not in d.columns:
        d["amount"] = 0.0
    d = d[(d["date"] >= start) & (d["date"] <= end)].copy()

    # 路径事件桶
    paths = {
        "A_weekly_ma20_hold": {5: [], 10: [], 20: []},
        "B_vol_dryup_thrust": {5: [], 10: [], 20: []},
        "C_rsi_cool_turn": {5: [], 10: [], 20: []},
        "C2_macd_cross_up": {5: [], 10: [], 20: []},
        "D_ma60_pb_bounce": {5: [], 10: [], 20: []},
        "E_liq_pool_random": {5: [], 10: [], 20: []},
        "A+B_combo": {5: [], 10: [], 20: []},
        "A+B+C_combo": {5: [], 10: [], 20: []},
    }

    groups = list(d.groupby("code", sort=False))
    n_codes = len(groups)
    rng = np.random.default_rng(42)
    t0 = time.perf_counter()

    for gi, (code, g) in enumerate(groups):
        if (gi + 1) % 500 == 0:
            print(f"  scan {gi+1}/{n_codes}", flush=True)
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < 280:
            continue
        name = str(g["name"].iloc[-1] if "name" in g.columns else "")
        if _is_st_name(name):
            continue

        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        high = pd.to_numeric(g["high"], errors="coerce").to_numpy(dtype=float)
        low = pd.to_numeric(g["low"], errors="coerce").to_numpy(dtype=float)
        amount = pd.to_numeric(g["amount"], errors="coerce").to_numpy(dtype=float)
        dates = g["date"].astype(str).to_numpy()

        cs = pd.Series(close)
        ma20 = cs.rolling(20, min_periods=20).mean().to_numpy()
        ma50 = cs.rolling(50, min_periods=50).mean().to_numpy()
        ma60 = cs.rolling(60, min_periods=60).mean().to_numpy()
        ema20 = cs.ewm(span=20, adjust=False).mean().to_numpy()
        adv20 = pd.Series(amount).rolling(20, min_periods=10).mean().to_numpy()
        a14 = atr(g, 14).to_numpy(dtype=float)
        rsi14 = _rsi(close, 14)
        dif, dea = _macd(close)

        # 周线近似：每 5 根日 K 取收盘 → 周 MA10 ≈ 50 日
        # 更稳：用日线 rolling 50 作「周线 MA10」代理；周线上升用 ma50 > ma50[10]
        # 真周线：按周 resample 最后交易日
        g2 = g.copy()
        g2["_dt"] = pd.to_datetime(g2["date"])
        w = (
            g2.set_index("_dt")
            .resample("W-FRI")
            .agg({"close": "last", "high": "max", "low": "min", "amount": "sum"})
            .dropna(subset=["close"])
        )
        if len(w) < 60:
            continue
        w_close = w["close"].to_numpy(dtype=float)
        w_ma10 = pd.Series(w_close).rolling(10, min_periods=10).mean().to_numpy()
        w_ma20 = pd.Series(w_close).rolling(20, min_periods=20).mean().to_numpy()
        # map week end -> weekly flags
        w_up = {}
        for i in range(len(w)):
            if not np.isfinite(w_ma10[i]) or not np.isfinite(w_ma20[i]):
                continue
            dt = w.index[i].strftime("%Y-%m-%d")
            w_up[dt] = bool(w_close[i] > w_ma10[i] > w_ma20[i] and w_ma10[i] > w_ma10[max(0, i - 2)])

        # 把周信号前推到该周内每个交易日（用最近已结束周）
        week_flag = np.zeros(len(g), dtype=bool)
        w_dates = sorted(w_up.keys())
        wi = 0
        last_flag = False
        for i, dt in enumerate(dates):
            while wi < len(w_dates) and w_dates[wi] <= dt:
                last_flag = w_up[w_dates[wi]]
                wi += 1
            week_flag[i] = last_flag

        for i in range(80, len(g) - 21):
            if dates[i] < start or dates[i] > end:
                continue
            c0 = float(close[i])
            if not np.isfinite(c0) or c0 <= 0:
                continue
            if not np.isfinite(adv20[i]) or float(adv20[i]) < min_adv:
                continue

            # 远期收益（收盘→收盘，事件研究；不扣成本）
            def add(path: str, ok: bool) -> None:
                if not ok:
                    return
                for n in (5, 10, 20):
                    paths[path][n].append(float(close[i + n]) / c0 - 1.0)

            a = float(a14[i]) if np.isfinite(a14[i]) else np.nan
            m20 = float(ma20[i])
            m50 = float(ma50[i])
            m60 = float(ma60[i])
            e20 = float(ema20[i])
            if not all(np.isfinite(x) for x in (m20, m50, m60, e20, a)) or a <= 0:
                continue

            # —— 公共：日线上升结构粗滤 ——
            ma60_up = m60 > float(ma60[i - 10]) if np.isfinite(ma60[i - 10]) else False
            above_ma50 = c0 > m50
            peak20 = float(np.nanmax(high[i - 19 : i + 1]))
            pullback_atr = (peak20 - c0) / a

            # 回踩 MA20/EMA20：最低触及带，收盘回到均线上方
            touched = float(np.nanmin(low[i - 2 : i + 1])) <= e20 * 1.01
            hold_above = c0 > e20 and c0 > float(close[i - 1])
            weekly_ok = bool(week_flag[i])

            # A: 周线多头 + 日线回踩 EMA20 企稳
            A = weekly_ok and above_ma50 and touched and hold_above and 0.5 <= pullback_atr <= 3.0
            add("A_weekly_ma20_hold", A)

            # B: 量价 — 近 10 日上涨段均量 > 回调段均量，确认日放量
            # 简化：过去 15 日分成前 8（冲高）后 5（回调）+ 当日
            amt_impulse = float(np.nanmean(amount[i - 14 : i - 5])) if i >= 15 else np.nan
            amt_pb = float(np.nanmean(amount[i - 5 : i])) if i >= 6 else np.nan
            amt_today = float(amount[i])
            vol_ok = (
                np.isfinite(amt_impulse)
                and np.isfinite(amt_pb)
                and amt_impulse > 0
                and amt_pb < amt_impulse * 0.75
                and amt_today > amt_pb * 1.2
                and amt_today > float(adv20[i]) * 0.9
            )
            B = vol_ok and ma60_up and above_ma50 and hold_above and 0.5 <= pullback_atr <= 3.0
            add("B_vol_dryup_thrust", B)

            # C: RSI 从 40-55 区间拐头向上（冷却后）
            r0, r1 = float(rsi14[i]), float(rsi14[i - 1]) if i >= 1 else np.nan
            C = (
                np.isfinite(r0)
                and np.isfinite(r1)
                and 38 <= r1 <= 55
                and r0 > r1
                and r0 < 65
                and ma60_up
                and above_ma50
                and hold_above
            )
            add("C_rsi_cool_turn", C)

            # C2: MACD 金叉且 DIF>0
            d0, e0 = float(dif[i]), float(dea[i])
            d1, e1 = float(dif[i - 1]), float(dea[i - 1])
            C2 = (
                np.isfinite(d0)
                and np.isfinite(e0)
                and np.isfinite(d1)
                and np.isfinite(e1)
                and d1 <= e1
                and d0 > e0
                and d0 > 0
                and ma60_up
            )
            add("C2_macd_cross_up", C2)

            # D: 旧近似 — MA60 上升 + ATR 回调 + 2 日反弹
            bounce = c0 / float(close[i - 2]) - 1.0 if close[i - 2] > 0 else -1
            D = (
                ma60_up
                and c0 > m60
                and 1.0 <= pullback_atr <= 2.5
                and bounce > 0.35 * (a / c0)
                and c0 > float(close[i - 1])
            )
            add("D_ma60_pb_bounce", D)

            # E: 流动性池随机（约 2% 抽样，作基线）
            if rng.random() < 0.02:
                add("E_liq_pool_random", True)

            # 组合
            add("A+B_combo", A and vol_ok)
            add("A+B+C_combo", A and vol_ok and 38 <= r1 <= 55 and r0 > r1)

        # 内存友好：不存逐事件明细

    elapsed = time.perf_counter() - t0
    print(f"scan done in {elapsed:.1f}s", flush=True)

    notes = {
        "A_weekly_ma20_hold": "周线 close>MA10>MA20 + 日线回踩EMA20企稳",
        "B_vol_dryup_thrust": "冲高放量、回调缩量、确认日放量",
        "C_rsi_cool_turn": "RSI冷却后拐头 + MA60多头",
        "C2_macd_cross_up": "MACD零轴上金叉 + MA60多头",
        "D_ma60_pb_bounce": "旧stage近似（无周线/量价）",
        "E_liq_pool_random": "同流动性池随机基线",
        "A+B_combo": "周线回踩 + 量价确认",
        "A+B+C_combo": "周线回踩 + 量价 + RSI",
    }
    out = []
    for k, buckets in paths.items():
        out.append(_pack(k, buckets, notes.get(k, "")))
    out.sort(key=lambda x: (-(x.fwd10_mean if np.isfinite(x.fwd10_mean) else -999), -x.n))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--min-adv", type=float, default=1.5e8)
    args = ap.parse_args()

    with home_context(args.home):
        from quant.store.paths import quant_home

        print("load daily…", flush=True)
        daily = load_adjusted_daily()
        print(
            f"rows={len(daily)} codes={daily['code'].nunique()} "
            f"date={daily['date'].min()}~{daily['date'].max()}",
            flush=True,
        )
        stats = explore(daily, args.start, args.end, min_adv=args.min_adv)
        out = Path(quant_home()) / "reports/bt_stage_swing"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"swing_path_explore_{args.start}_{args.end}.json"
        payload = {
            "start": args.start,
            "end": args.end,
            "min_adv": args.min_adv,
            "paths": [asdict(s) for s in stats],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print("\n=== PATH RANK by fwd10 mean ===", flush=True)
        for s in stats:
            print(
                f"{s.path:22s} n={s.n:6d}  "
                f"5d={s.fwd5_mean:+6.2f}%/{s.fwd5_win:4.1f}%  "
                f"10d={s.fwd10_mean:+6.2f}%/{s.fwd10_win:4.1f}%  "
                f"20d={s.fwd20_mean:+6.2f}%/{s.fwd20_win:4.1f}%  "
                f"| {s.notes}",
                flush=True,
            )
        print("DONE", path, flush=True)


if __name__ == "__main__":
    main()
