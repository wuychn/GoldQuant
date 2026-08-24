"""成本敏感性：WF 最优配置。"""
from datetime import date
from pathlib import Path
import pickle
import pandas as pd
from scripts.cli_home import add_home_argument, home_context
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from scripts.research.swing_seek_fast import precompute_ranks
from scripts.research.swing_seek_wf_refine import walk_forward
from scripts.research.swing_monthly10_explore import monthly_stats

def main():
    import argparse
    ap = argparse.ArgumentParser()
    add_home_argument(ap)
    args = ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home
        daily = load_adjusted_daily()
        dates = [to_iso(d) for d in trading_day_list(date(2023,8,1), date(2025,12,31))]
        with open(Path(quant_home())/"reports/bt_swing_band/alpha_2023-08-01_2025-12-31.pkl","rb") as f:
            alpha = pickle.load(f)
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        d = d[d["date"].isin(dates)]
        close = d.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(dates)
        ret = close.pct_change()
        ranks = precompute_ranks(alpha, dates, set(close.columns.astype(str)))
        for cost in (0.0, 0.001, 0.002, 0.003, 0.005):
            curve = walk_forward(ret, ranks, dates, 1, 20, lb_months=4, cost_rt=cost, cash_if_best_neg=True, min_edge=0.05)
            ms = monthly_stats(curve)
            tot = (curve[-1][1]/curve[0][1]-1)*100
            print(f"cost={cost:.3f} meanM={ms.get('mean_month_pct')} med={ms.get('median_month_pct')} ge10={ms.get('pct_months_ge_10')}% ret={tot:.1f}% best={ms.get('best_month_pct')} worst={ms.get('worst_month_pct')}", flush=True)

if __name__ == "__main__":
    main()
