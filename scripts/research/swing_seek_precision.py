"""精确冲击：cost∈{0.0005,0.001,0.0015}，halt 步长 0.002。"""
from __future__ import annotations
import json, pickle
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.exit.atr import atr
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_monthly10_explore import monthly_stats
from scripts.research.swing_seek_fast import precompute_ranks
from scripts.research.swing_seek_final_push import run

def main():
    import argparse
    ap=argparse.ArgumentParser(); add_home_argument(ap); args=ap.parse_args()
    with home_context(args.home):
        from quant.store.paths import quant_home
        out=Path(quant_home())/"reports/bt_swing_seek"
        daily=load_adjusted_daily()
        dates=[to_iso(d) for d in trading_day_list(date(2023,8,1), date(2025,12,31))]
        with open(Path(quant_home())/"reports/bt_swing_band/alpha_2023-08-01_2025-12-31.pkl","rb") as f:
            alpha=pickle.load(f)
        d=daily.copy(); d["date"]=pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d"); d["code"]=d["code"].astype(str)
        d=d[d["date"].isin(dates)]
        close=d.pivot_table(index="date",columns="code",values="close",aggfunc="last").reindex(dates)
        atr_cols={}
        for code,g in d.groupby("code"):
            g=g.sort_values("date")
            if len(g)<30: continue
            atr_cols[str(code)]=atr(g,14).groupby(g["date"].values).last()
        atr14=pd.DataFrame(atr_cols).reindex(dates)
        ranks=precompute_ranks(alpha,dates,set(close.columns.astype(str)))
        best=None
        rows=[]
        for trail in (3.0, 3.05, 3.1):
            for halt in np.arange(-0.160, -0.181, -0.002):
                for cost in (0.0005, 0.001, 0.0015, 0.002):
                    curve=run(close,atr14,ranks,dates,trail,float(halt),cost,False)
                    ms=monthly_stats(curve)
                    tot=(curve[-1][1]/curve[0][1]-1)*100
                    mm=float(ms.get("mean_month_pct") or 0)
                    row={"trail":trail,"halt":float(halt),"cost":cost,"meanM":mm,"ret":round(tot,2),
                         "ge10":ms.get("pct_months_ge_10"),"med":ms.get("median_month_pct"),
                         "worst":ms.get("worst_month_pct"),"best":ms.get("best_month_pct")}
                    rows.append(row)
                    if best is None or mm>best["meanM"]:
                        best=row
                    if mm>=10:
                        print("SUCCESS",row,flush=True)
        rows.sort(key=lambda r:-r["meanM"])
        (out/"enhance_precision.json").write_text(json.dumps({"best":best,"top":rows[:20]},indent=2),encoding="utf-8")
        print("BEST",best,flush=True)
        print("TOP5",flush=True)
        for r in rows[:5]:
            print(r,flush=True)
        print("DONE",flush=True)

if __name__=="__main__":
    main()
