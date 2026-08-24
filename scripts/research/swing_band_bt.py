"""波段状态机回测。

用法::

    poetry run python -m scripts.research.swing_band_bt --home D:/ProgramData/.quant
    poetry run python -m scripts.research.swing_band_bt --home D:/ProgramData/.quant \\
        --variant v3_trail25 --trail-atr-mult 2.5 --reuse-cache
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import time
from datetime import date
from pathlib import Path

from quant.backtest.engine import run_backtest
from quant.backtest.metrics import compute_metrics
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.swing.policy import SwingBandPolicy
from quant.swing.signals import SwingBandParams
from scripts.cli_home import add_home_argument, home_context


def _params_from_args(args: argparse.Namespace) -> SwingBandParams:
    """CLI 覆盖；未传则用 dataclass / yml 默认。"""
    from quant.config import load_quant_config

    raw = (load_quant_config().get("swing_band") or {})
    kw = {
        "n_lookback": int(raw.get("n_lookback", 20)),
        "atr_period": int(raw.get("atr_period", 14)),
        "pullback_atr_mult": float(raw.get("pullback_atr_mult", 0.8)),
        "pullback_atr_max": float(raw.get("pullback_atr_max", 2.5)),
        "bounce_days": int(raw.get("bounce_days", 3)),
        "bounce_atr_mult": float(raw.get("bounce_atr_mult", 0.3)),
        "max_run_atr_mult": float(raw.get("max_run_atr_mult", 2.0)),
        "require_ma_rising": bool(raw.get("require_ma_rising", True)),
        "dist_high_pctile": float(raw.get("dist_high_pctile", 50)),
        "require_above_ma20": bool(raw.get("require_above_ma20", True)),
        "ma_period": int(raw.get("ma_period", 20)),
        "trail_atr_mult": float(raw.get("trail_atr_mult", 3.5)),
        "stall_enabled": bool(raw.get("stall_enabled", False)),
        "stall_hold_days": int(raw.get("stall_hold_days", 30)),
        "stall_atr_mult": float(raw.get("stall_atr_mult", 0.5)),
        "time_stop_days": int(raw.get("time_stop_days", 45)),
    }
    overrides = {
        "n_lookback": args.n_lookback,
        "pullback_atr_mult": args.pullback_atr_mult,
        "pullback_atr_max": args.pullback_atr_max,
        "bounce_days": args.bounce_days,
        "bounce_atr_mult": args.bounce_atr_mult,
        "max_run_atr_mult": args.max_run_atr_mult,
        "require_ma_rising": args.require_ma_rising,
        "dist_high_pctile": args.dist_high_pctile,
        "require_above_ma20": args.require_above_ma20,
        "trail_atr_mult": args.trail_atr_mult,
        "stall_enabled": args.stall_enabled,
        "time_stop_days": args.time_stop_days,
    }
    for k, v in overrides.items():
        if v is not None:
            kw[k] = v
    return SwingBandParams(**kw)


def _buy_cache_key(params: SwingBandParams) -> str:
    """买入侧参数决定 cache；卖出参数不参与。"""
    buy_fields = (
        params.buy_mode,
        params.n_lookback,
        params.atr_period,
        params.pullback_atr_mult,
        params.pullback_atr_max,
        params.bounce_days,
        params.bounce_atr_mult,
        params.max_run_atr_mult,
        params.require_ma_rising,
        params.ma_slope_lookback,
        params.dist_high_pctile,
        params.require_above_ma20,
        params.ma_period,
        params.dist_high_lookback,
        params.momentum_atr_mult,
    )
    h = hashlib.sha1(repr(buy_fields).encode()).hexdigest()[:10]
    return f"buy_cache_{h}.pkl"


def main() -> None:
    ap = argparse.ArgumentParser(description="波段状态机回测")
    add_home_argument(ap)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--variant", default="v3", help="结果文件后缀")
    ap.add_argument("--max-stocks", type=int, default=None)
    ap.add_argument("--max-weight", type=float, default=None)
    ap.add_argument("--full-invest", type=float, default=0.95)
    ap.add_argument("--equal-weight-new", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--reuse-cache", action="store_true", help="强制复用已有 buy_cache（同买入参数）")
    # 买入
    ap.add_argument("--n-lookback", type=int, default=None)
    ap.add_argument("--pullback-atr-mult", type=float, default=None)
    ap.add_argument("--pullback-atr-max", type=float, default=None)
    ap.add_argument("--bounce-days", type=int, default=None)
    ap.add_argument("--bounce-atr-mult", type=float, default=None)
    ap.add_argument("--max-run-atr-mult", type=float, default=None)
    ap.add_argument("--dist-high-pctile", type=float, default=None)
    ap.add_argument("--require-ma-rising", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--require-above-ma20", action=argparse.BooleanOptionalAction, default=None)
    # 卖出
    ap.add_argument("--trail-atr-mult", type=float, default=None)
    ap.add_argument("--stall-enabled", action=argparse.BooleanOptionalAction, default=None)
    ap.add_argument("--time-stop-days", type=int, default=None)
    args = ap.parse_args()

    with home_context(args.home):
        from quant.config import load_quant_config
        from quant.store.paths import quant_home

        yml = load_quant_config().get("swing_band") or {}
        max_stocks = int(args.max_stocks if args.max_stocks is not None else yml.get("max_stocks", 5))
        max_weight = float(args.max_weight if args.max_weight is not None else yml.get("max_weight", 0.22))

        out = Path(quant_home()) / "reports/bt_swing_band"
        out.mkdir(parents=True, exist_ok=True)

        print("load daily …", flush=True)
        daily = load_adjusted_daily()
        dates = [
            to_iso(d)
            for d in trading_day_list(date.fromisoformat(args.start), date.fromisoformat(args.end))
        ]
        print(f"dates={len(dates)} {dates[0]}..{dates[-1]}", flush=True)

        params = _params_from_args(args)
        pol = SwingBandPolicy(
            daily=daily,
            params=params,
            max_stocks=max_stocks,
            full_invest=float(args.full_invest),
            max_weight=max_weight,
            equal_weight_new=bool(args.equal_weight_new),
        )
        t0 = time.perf_counter()
        cache_path = out / _buy_cache_key(params)
        print(f"buy_cache → {cache_path.name}", flush=True)
        if cache_path.exists():
            with open(cache_path, "rb") as f:
                pol.buy_cache = pickle.load(f)
            d = daily
            if not __import__("pandas").api.types.is_string_dtype(d["date"]):
                d = d.copy()
                d["date"] = __import__("pandas").to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
            d["code"] = d["code"].astype(str)
            pol.daily = d
            pol._by_code = {
                str(c): g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")
            }
            pol._prepared = True
            print(f"loaded buy_cache from {cache_path}", flush=True)
        else:
            if args.reuse_cache:
                raise SystemExit(f"--reuse-cache 但找不到 {cache_path}")
            print("precompute buy cache …", flush=True)
            pol.prepare(dates)
            with open(cache_path, "wb") as f:
                pickle.dump(pol.buy_cache, f)
            print(f"saved buy_cache → {cache_path}", flush=True)
        print(f"precompute {time.perf_counter()-t0:.1f}s", flush=True)

        def alpha_fn(_d, _rows):
            return {"__swing__": 1.0}

        print("run_backtest …", flush=True)
        t1 = time.perf_counter()
        broker = run_backtest(
            daily=daily,
            dates=dates,
            alpha_fn=alpha_fn,
            policy=pol,
            max_positions=max_stocks,
            exit_config=None,
            strict_signals=True,
            drawdown_halt=None,
        )
        m = compute_metrics(broker, daily=None)
        cost = sum(float(t.cost or 0) for t in broker.trades)
        row = {
            "variant": args.variant,
            "ret": m["total_return_pct"],
            "ann": m["ann_return_pct"],
            "sharpe": m["sharpe"],
            "mdd": m["max_drawdown_pct"],
            "eq": m["final_equity"],
            "n_trades": m["n_trades"],
            "turn_ann": m["turnover_annual"],
            "avg_hold": m["avg_hold_days"],
            "win_rate": m["win_rate"],
            "cost": round(cost, 2),
            "cost_pct": round(100.0 * cost / 1_000_000.0, 2),
            "sec_bt": round(time.perf_counter() - t1, 1),
            "sec_total": round(time.perf_counter() - t0, 1),
            "max_positions": max_stocks,
            "max_weight": max_weight,
            "params": {
                "n_lookback": params.n_lookback,
                "pullback_atr_mult": params.pullback_atr_mult,
                "pullback_atr_max": params.pullback_atr_max,
                "bounce_atr_mult": params.bounce_atr_mult,
                "max_run_atr_mult": params.max_run_atr_mult,
                "dist_high_pctile": params.dist_high_pctile,
                "trail_atr_mult": params.trail_atr_mult,
                "time_stop_days": params.time_stop_days,
                "stall_enabled": params.stall_enabled,
                "require_ma_rising": params.require_ma_rising,
            },
        }
        out_json = out / f"metrics_{args.variant}.json"
        out_json.write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        (out / "metrics.json").write_text(json.dumps(row, indent=2, default=str), encoding="utf-8")
        print(row, flush=True)
        print("DONE", out_json, flush=True)


if __name__ == "__main__":
    main()
