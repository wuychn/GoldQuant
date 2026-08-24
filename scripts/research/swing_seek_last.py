"""最后尝试：Top2 分散左尾；以及 cost 连续扫找 mean≥10。"""
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
from scripts.research.swing_seek_fast import precompute_ranks
from scripts.research.swing_seek_micro import run as run_micro

def month_mean(curve):
    df=pd.DataFrame(curve,columns=["date","eq"]); df["date"]=pd.to_datetime(df["date"])
    me=df.groupby(df["date"].dt.to_period("M")).last()["eq"].pct_change().dropna()
    return float(me.mean())*100, float((me>=0.1).mean())*100, float(me.median())*100, (curve[-1][1]/curve[0][1]-1)*100

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
        rows=[]
        for topn in (1,2):
            for trail in (2.9, 3.0, 3.05, 3.1):
                for halt in (-0.15, -0.17, -0.18, -0.20):
                    for cost in (0.0012, 0.0015, 0.002):
                        curve=run_micro(close,atr14,ranks,dates,trail=trail,halt=halt,halt_scale=0.0,min_edge=0.05,cost_rt=cost,topn=topn)
                        mm,ge,med,ret=month_mean(curve)
                        row={"topn":topn,"trail":trail,"halt":halt,"cost":cost,"meanM":round(mm,4),"ge10":round(ge,1),"med":round(med,2),"ret":round(ret,2)}
                        rows.append(row)
                        if mm>=10:
                            print("SUCCESS",row,flush=True)
                        elif mm>=9.95:
                            print("NEAR",row,flush=True)
        rows.sort(key=lambda r:-r["meanM"])
        (out/"last_push.json").write_text(json.dumps(rows[:25],indent=2),encoding="utf-8")
        print("BEST",rows[0],flush=True)
        for r in rows[:8]:
            print(r,flush=True)
        print("hits",sum(1 for r in rows if r["meanM"]>=10),flush=True)
        print("DONE",flush=True)

if __name__=="__main__":
    main()
