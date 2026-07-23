"""扩展分析：持仓周期、时段、市场环境、未平仓。"""
from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\ProgramData\.quant")


def load_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def parse_score(reason: str) -> float | None:
    m = re.search(r"评分(\d+\.?\d*)", reason or "")
    return float(m.group(1)) if m else None


def market_context(date: str) -> dict:
    for mode in ("pre_market", "evening"):
        raw = load_json(ROOT / "daily" / date / "raw" / (f"{mode}.json" if mode != "evening" else "evening.json"))
        if not raw:
            continue
        data = raw.get("data", raw)
        idx = (data.get("大盘指数") or [{}])[0] if isinstance(data.get("大盘指数"), list) else {}
        sent = data.get("赚钱效应") or {}
        return {
            "上证涨跌": idx.get("涨跌幅") or idx.get("涨跌"),
            "上涨家数": sent.get("上涨") or sent.get("涨"),
            "下跌家数": sent.get("下跌") or sent.get("跌"),
            "涨停": sent.get("涨停"),
        }
    return {}


def holding_days(buy_d: str, sell_d: str) -> int:
    try:
        b = datetime.strptime(buy_d, "%Y-%m-%d")
        s = datetime.strptime(sell_d, "%Y-%m-%d")
        return (s - b).days
    except ValueError:
        return -1


def main() -> None:
    daily_dirs = sorted(p.name for p in (ROOT / "daily").iterdir() if p.is_dir())

    all_trades: list[dict] = []
    for d in daily_dirs:
        rows = load_json(ROOT / "daily" / d / "trades" / "executed.json")
        if isinstance(rows, list):
            for t in rows:
                t = dict(t)
                t["_date"] = d
                all_trades.append(t)

    by_code: dict[str, list[dict]] = defaultdict(list)
    for t in sorted(all_trades, key=lambda x: (x["_date"], x.get("时间", ""))):
        by_code[t["股票代码"]].append(t)

    round_trips = []
    open_positions = []
    for code, trades in by_code.items():
        queue = []
        for t in trades:
            if t.get("方向") == "买入":
                queue.append(t)
            elif t.get("方向") == "卖出":
                if queue:
                    b = queue.pop(0)
                    pnl = float(t.get("已实现盈亏") or 0)
                    buy_price = float(b.get("成交价") or 0)
                    sell_price = float(t.get("成交价") or 0)
                    ret_pct = (sell_price - buy_price) / buy_price * 100 if buy_price else 0
                    round_trips.append(
                        {
                            "code": code,
                            "name": t.get("股票名称"),
                            "buy_date": b["_date"],
                            "sell_date": t["_date"],
                            "buy_time": b.get("时间", ""),
                            "sell_time": t.get("时间", ""),
                            "hold_days": holding_days(b["_date"], t["_date"]),
                            "score": parse_score(b.get("理由", "")),
                            "sell_type": t.get("卖出类型", ""),
                            "pnl": pnl,
                            "ret_pct": ret_pct,
                            "buy_market": market_context(b["_date"]),
                        }
                    )
                else:
                    pass
        for b in queue:
            open_positions.append(b)

    print("=== 持仓周期 ===")
    for rt in sorted(round_trips, key=lambda x: x["hold_days"]):
        print(
            f"  {rt['code']} {rt['name'][:4]} 持{rt['hold_days']}天 "
            f"买{rt['buy_date']} {rt['buy_time'][:5]} "
            f"ret={rt['ret_pct']:.1f}% pnl={rt['pnl']:.0f} [{rt['sell_type']}]"
        )
    if round_trips:
        print(f"  平均持仓: {statistics.mean(r['hold_days'] for r in round_trips):.1f} 天")

    print("\n=== 买入时段 vs 盈亏 ===")
    buckets = {"09:30-10:00": [], "10:00-11:30": [], "13:00-14:30": [], "14:30+": []}
    for rt in round_trips:
        t = rt["buy_time"]
        h, m = (int(t[:2]), int(t[3:5])) if len(t) >= 5 else (0, 0)
        mins = h * 60 + m
        if mins < 600:
            key = "09:30-10:00"
        elif mins < 690:
            key = "10:00-11:30"
        elif mins < 870:
            key = "13:00-14:30"
        else:
            key = "14:30+"
        buckets[key].append(rt["pnl"])
    for k, pnls in buckets.items():
        if pnls:
            print(f"  {k}: n={len(pnls)} 总={sum(pnls):.0f} 均={statistics.mean(pnls):.0f}")

    print("\n=== 买入日市场环境(盘前) ===")
    for rt in sorted(round_trips, key=lambda x: x["pnl"]):
        m = rt["buy_market"]
        print(
            f"  {rt['buy_date']} {rt['code'][:6]} pnl={rt['pnl']:.0f} "
            f"上证{m.get('上证涨跌','?')}% 涨{m.get('上涨家数','?')}/跌{m.get('下跌家数','?')}"
        )

    print("\n=== 未平仓 ===")
    for b in open_positions:
        code = b["股票代码"]
        buy_price = float(b.get("成交价") or 0)
        # latest price from during snapshot
        latest_price = None
        for d in reversed(daily_dirs):
            raw_dir = ROOT / "daily" / d / "raw"
            if not raw_dir.is_dir():
                continue
            for fp in sorted(raw_dir.glob("during*.json"), reverse=True):
                obj = load_json(fp)
                if not obj:
                    continue
                data = obj.get("data", obj)
                for key in ("持仓股", "自选股"):
                    for row in data.get(key) or []:
                        if row.get("股票代码") == code:
                            q = (row.get("盘口") or {})
                            latest_price = q.get("最新") or row.get("最新价")
                            if latest_price:
                                break
                if latest_price:
                    break
            if latest_price:
                break
        unrealized = (float(latest_price) - buy_price) * int(b.get("股数") or 0) if latest_price else None
        print(
            f"  {code} {b.get('股票名称')} 买{b['_date']} @{buy_price:.2f} "
            f"现{latest_price} 浮盈{unrealized:.0f}" if unrealized is not None else f"  {code} no price"
        )

    # dimension analysis on buy scores from scores_holding or during
    print("\n=== 亏损单买入时 main_wave/动能 (抽样) ===")
    for rt in sorted(round_trips, key=lambda x: x["pnl"])[:5]:
        d = rt["buy_date"]
        found = None
        for fp in sorted((ROOT / "daily" / d / "raw").glob("during*.json")):
            obj = load_json(fp)
            if not obj:
                continue
            data = obj.get("data", obj)
            for row in data.get("自选股") or []:
                if row.get("股票代码") == rt["code"]:
                    found = row
        if found:
            chg = (found.get("盘口") or {}).get("涨跌幅") or found.get("涨跌幅")
            print(f"  {rt['code']} 买日涨跌幅={chg} score={rt['score']}")

    # check user config
    cfg = load_json(ROOT / "config" / "quant.yml")
    print("\n=== 用户配置覆盖 ===")
    if cfg:
        scoring = cfg.get("scoring") or {}
        gates = cfg.get("gates") or {}
        print(f"  buy_threshold: {scoring.get('buy_threshold')}")
        print(f"  entry/exit: {scoring.get('watchlist_entry_threshold')}/{scoring.get('watchlist_exit_threshold')}")
        mw = gates.get("main_wave") or {}
        print(f"  stop_loss_pct: {mw.get('stop_loss_pct')}")
        print(f"  intraday_weak: {gates.get('sell', {}).get('intraday_weak')}")
    else:
        print("  无 ~/.quant/config/quant.yml 覆盖，使用包内默认")

    # evening watchlist churn
    print("\n=== 晚间达标率趋势 ===")
    for d in daily_dirs:
        rows = load_json(ROOT / "daily" / d / "derived" / "scores_watchlist.json")
        if not isinstance(rows, list) or not rows:
            continue
        passed = sum(1 for r in rows if r.get("达标"))
        mw_pass = 0
        for r in rows:
            if not r.get("达标"):
                continue
            for dim in r.get("分项") or []:
                if dim.get("维度") == "main_wave" and (dim.get("详情") or {}).get("主升波段"):
                    mw_pass += 1
                    break
        print(f"  {d}: 候选{len(rows)} 达标{passed} 主升达标{mw_pass}")


if __name__ == "__main__":
    main()
