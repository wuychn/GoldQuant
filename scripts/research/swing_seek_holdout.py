"""样本外：2023-08~2024-12 定参，2025 验证月均。"""
from __future__ import annotations
import json, pickle
from datetime import date
from pathlib import Path
import pandas as pd
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.exit.atr import atr
from scripts.cli_home import add_home_argument, home_context
from scripts.research.swing_seek_fast import precompute_ranks
from scripts.research.swing_seek_micro import run as run_micro

def month_stats(curve, start=None, end=None):
    df=pd.DataFrame(curve,columns=["date","eq"]); df["date"]=pd.to_datetime(df["date"])
    if start: df=df[df["date"]>=start]
    if end: df=df[df["date"]<=end]
    me=df.groupby(df["date"].dt.to_period("M")).last()["eq"].pct_change().dropna()
    if len(me)==0: return {}
    return {
        "n":len(me),
        "meanM":round(float(me.mean())*100,4),
        "med":round(float(me.median())*100,2),
        "ge10":round(float((me>=0.1).mean())*100,1),
        "best":round(float(me.max())*100,2),
        "worst":round(float(me.min())*100,2),
        "ret":round((df["eq"].iloc[-1]/df["eq"].iloc[0]-1)*100,2),
        "monthly":{str(p):round(float(r)*100,2) for p,r in me.items()},
    }

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

        # 定参：train 窗选最优
        train_dates=[x for x in dates if x<="2024-12-31"]
        best=None
        for trail in (2.9,3.0,3.05,3.1):
            for halt in (-0.15,-0.17,-0.18,-0.20):
                curve=run_micro(close,atr14,ranks,dates,trail=trail,halt=halt,cost_rt=0.0015,topn=1)
                # 只看 train 段权益重建：用全曲线但 stats 截断
                st=month_stats(curve,"2023-09-01","2024-12-31")
                if best is None or st["meanM"]>best["meanM"]:
                    best={"trail":trail,"halt":halt,**st}
        print("TRAIN_BEST",best,flush=True)

        curve=run_micro(close,atr14,ranks,dates,trail=best["trail"],halt=best["halt"],cost_rt=0.0015,topn=1)
        full=month_stats(curve)
        oos=month_stats(curve,"2025-01-01","2025-12-31")
        train=month_stats(curve,"2023-09-01","2024-12-31")
        y2024=month_stats(curve,"2024-01-01","2024-12-31")
        summary={
            "params":{"trail":best["trail"],"halt":best["halt"],"every":20,"lb":4,"topn":1,"cost":0.0015},
            "train_2023_2024":train,
            "oos_2025":oos,
            "y2024":y2024,
            "full":full,
            "target_train":train.get("meanM",0)>=10,
            "target_oos":oos.get("meanM",0)>=10,
            "target_full":full.get("meanM",0)>=10,
        }
        path=out/"holdout_summary.json"
        path.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
        print("TRAIN",train,flush=True)
        print("OOS2025",oos,flush=True)
        print("Y2024",y2024,flush=True)
        print("FULL",full,flush=True)
        print("DONE",path,flush=True)

if __name__=="__main__":
    main()
