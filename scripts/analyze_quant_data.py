"""临时分析 D:\\ProgramData\\.quant 数据，输出策略诊断。"""
from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(r"D:\ProgramData\.quant")


def load_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def parse_score_from_reason(reason: str) -> float | None:
    m = re.search(r"评分(\d+\.?\d*)", reason or "")
    return float(m.group(1)) if m else None


def parse_buy_type(reason: str) -> str:
    if "上升途中" in (reason or ""):
        return "上升途中"
    if "回调" in (reason or ""):
        return "回调企稳"
    return "未知"


def main() -> None:
    daily_dirs = sorted(p.name for p in (ROOT / "daily").iterdir() if p.is_dir())
    print("=== 数据概况 ===")
    print(f"交易日目录: {len(daily_dirs)}  ({daily_dirs[0]} ~ {daily_dirs[-1]})")

    during_counts = []
    for d in daily_dirs:
        raw = ROOT / "daily" / d / "raw"
        if raw.is_dir():
            during_counts.append(len(list(raw.glob("during*.json"))))
    if during_counts:
        print(
            f"盘中快照/日: 均{statistics.mean(during_counts):.0f} "
            f"min{min(during_counts)} max{max(during_counts)}"
        )

    # --- trades ---
    all_trades: list[dict] = []
    for d in daily_dirs:
        rows = load_json(ROOT / "daily" / d / "trades" / "executed.json")
        if isinstance(rows, list):
            for t in rows:
                t = dict(t)
                t["_date"] = d
                all_trades.append(t)

    buys = [t for t in all_trades if t.get("方向") == "买入"]
    sells = [t for t in all_trades if t.get("方向") == "卖出"]
    print(f"\n=== 成交 ({len(all_trades)} 笔: 买{len(buys)} 卖{len(sells)}) ===")

    sell_pnl = [float(t.get("已实现盈亏") or 0) for t in sells]
    total_realized = sum(sell_pnl)
    wins = [p for p in sell_pnl if p > 0]
    losses = [p for p in sell_pnl if p <= 0]
    print(f"已实现盈亏合计: {total_realized:.2f}")
    if sells:
        print(f"卖出笔数: {len(sells)}, 胜率: {len(wins)/len(sells)*100:.1f}%")
        print(f"均赢: {statistics.mean(wins):.2f}" if wins else "均赢: -")
        print(f"均亏: {statistics.mean(losses):.2f}" if losses else "均亏: -")
        pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 0
        print(f"盈亏比(总额): {pf:.2f}")

    # pair round trips by code (FIFO)
    by_code: dict[str, list[dict]] = defaultdict(list)
    for t in sorted(all_trades, key=lambda x: (x["_date"], x.get("时间", ""))):
        by_code[t["股票代码"]].append(t)

    round_trips: list[dict] = []
    for code, trades in by_code.items():
        queue: list[dict] = []
        for t in trades:
            if t.get("方向") == "买入":
                queue.append(t)
            elif t.get("方向") == "卖出" and queue:
                b = queue.pop(0)
                pnl = float(t.get("已实现盈亏") or 0)
                round_trips.append(
                    {
                        "code": code,
                        "name": t.get("股票名称", ""),
                        "buy_date": b["_date"],
                        "sell_date": t["_date"],
                        "buy_score": parse_score_from_reason(b.get("理由", "")),
                        "buy_type": parse_buy_type(b.get("理由", "")),
                        "sell_type": t.get("卖出类型", ""),
                        "pnl": pnl,
                        "buy_price": float(b.get("成交价") or 0),
                        "sell_price": float(t.get("成交价") or 0),
                    }
                )

    print(f"\n=== 完整回合 ({len(round_trips)} 笔) ===")
    for rt in sorted(round_trips, key=lambda x: x["pnl"]):
        print(
            f"  {rt['sell_date']} {rt['code']} {rt['name'][:4]} "
            f"买{rt['buy_date']} 分{rt['buy_score']} {rt['buy_type']} "
            f"卖[{rt['sell_type']}] pnl={rt['pnl']:.0f}"
        )

    # stats by dimension
    def bucket_stats(label: str, items: list[dict], key_fn):
        groups: dict[str, list[float]] = defaultdict(list)
        for rt in items:
            groups[key_fn(rt)].append(rt["pnl"])
        print(f"\n--- 按{label} ---")
        for k in sorted(groups, key=lambda x: statistics.mean(groups[x]) if groups[x] else 0):
            pnls = groups[k]
            wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            print(f"  {k}: n={len(pnls)} 总pnl={sum(pnls):.0f} 均{statistics.mean(pnls):.0f} 胜率{wr:.0f}%")

    if round_trips:
        bucket_stats("买入类型", round_trips, lambda r: r["buy_type"])
        bucket_stats("卖出类型", round_trips, lambda r: r["sell_type"] or "未知")
        bucket_stats(
            "买入评分",
            round_trips,
            lambda r: (
                ">=80"
                if r["buy_score"] and r["buy_score"] >= 80
                else "72-79"
                if r["buy_score"] and r["buy_score"] >= 72
                else "<72"
                if r["buy_score"]
                else "未知"
            ),
        )

    # --- scores_watchlist forward analysis ---
    samples: list[dict] = []
    for i, d in enumerate(daily_dirs):
        rows = load_json(ROOT / "daily" / d / "derived" / "scores_watchlist.json")
        if not isinstance(rows, list):
            continue
        next_d = daily_dirs[i + 1] if i + 1 < len(daily_dirs) else None
        next_rows = (
            load_json(ROOT / "daily" / next_d / "derived" / "scores_watchlist.json")
            if next_d
            else None
        )
        next_map = {}
        if isinstance(next_rows, list):
            for r in next_rows:
                next_map[r.get("股票代码")] = r

        for row in rows:
            code = row.get("股票代码")
            total = row.get("总分")
            if code is None or total is None:
                continue
            passed = bool(row.get("达标"))
            main_wave = None
            for dim in row.get("分项") or []:
                if dim.get("维度") == "main_wave":
                    det = dim.get("详情") or {}
                    main_wave = det.get("主升波段")
                    break
            next_total = None
            if code in next_map:
                next_total = next_map[code].get("总分")
            samples.append(
                {
                    "date": d,
                    "code": code,
                    "total": float(total),
                    "passed": passed,
                    "main_wave": main_wave,
                    "next_total_delta": (
                        float(next_total) - float(total)
                        if next_total is not None
                        else None
                    ),
                }
            )

    print(f"\n=== 候选池评分样本 ({len(samples)} 条) ===")
    if samples:
        passed = [s for s in samples if s["passed"]]
        failed = [s for s in samples if not s["passed"]]
        print(f"达标(>=entry): {len(passed)} ({len(passed)/len(samples)*100:.1f}%)")
        with_delta = [s for s in samples if s["next_total_delta"] is not None]
        if with_delta:
            up = sum(1 for s in with_delta if s["next_total_delta"] > 0)
            print(f"次日评分上升比例: {up/len(with_delta)*100:.1f}% (n={len(with_delta)})")

        for label, filt in [
            ("达标股", lambda s: s["passed"]),
            ("未达标", lambda s: not s["passed"]),
            ("主升=True且达标", lambda s: s["passed"] and s["main_wave"] is True),
            ("总分>=80", lambda s: s["total"] >= 80),
            ("总分72-79", lambda s: 72 <= s["total"] < 80),
        ]:
            sub = [s for s in with_delta if filt(s)]
            if len(sub) >= 5:
                up = sum(1 for s in sub if s["next_total_delta"] > 0) / len(sub) * 100
                print(f"  {label}: n={len(sub)} 次日评分上升{up:.0f}%")

    # --- signals analysis ---
    buy_signals = 0
    sell_signals = 0
    exec_signals = 0
    rejected_days = 0
    for d in daily_dirs:
        sig = load_json(ROOT / "daily" / d / "derived" / "signals.json")
        if not isinstance(sig, dict):
            continue
        buy_signals += len(sig.get("raw_buy") or [])
        sell_signals += len(sig.get("raw_sell") or [])
        exec_signals += len(sig.get("executable") or [])

    print(f"\n=== 信号统计 ===")
    print(f"原始买信号累计: {buy_signals}, 原始卖: {sell_signals}, 可执行: {exec_signals}")

    # --- account ---
    acct = load_json(ROOT / "state" / "account.json")
    if acct:
        print(f"\n=== 当前账户 ===")
        for k, v in acct.items():
            print(f"  {k}: {v}")

    # --- stoploss history ---
    stoploss = ROOT / "state" / "stoploss.jsonl"
    if stoploss.is_file():
        lines = [l for l in stoploss.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"\n=== 止损冷却记录 ({len(lines)} 条) ===")
        for line in lines[-10:]:
            print(f"  {line[:120]}")

    # --- optional pool size over time ---
    print(f"\n=== 自选池规模(晚间 scores 行数 proxy) ===")
    for d in daily_dirs[-8:]:
        rows = load_json(ROOT / "daily" / d / "derived" / "scores_watchlist.json")
        n = len(rows) if isinstance(rows, list) else 0
        passed_n = sum(1 for r in (rows or []) if r.get("达标")) if rows else 0
        print(f"  {d}: 候选{n} 达标{passed_n}")


if __name__ == "__main__":
    main()
