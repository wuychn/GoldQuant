"""盘点 D:\\ProgramData\\.quant 历史数据字段覆盖情况。"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"D:\ProgramData\.quant")


def load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def sample_stock_keys(payload: dict) -> set[str]:
    keys: set[str] = set()
    for bucket in (
        "自选股",
        "持仓股",
        "同花顺人气榜",
        "创新高",
        "持续上涨",
        "持续放量",
        "量价齐升",
    ):
        rows = payload.get(bucket) or []
        if rows and isinstance(rows[0], dict):
            keys.update(rows[0].keys())
            # nested
            for k, v in rows[0].items():
                if isinstance(v, dict):
                    keys.add(f"{k}.*")
                elif isinstance(v, list) and v and isinstance(v[0], dict):
                    keys.add(f"{k}[0].*")
                    keys.update(f"{k}[].{x}" for x in v[0].keys())
    return keys


def hist_bar_fields(stock: dict) -> set[str]:
    hist = stock.get("历史行情") or []
    if not hist or not isinstance(hist[0], dict):
        return set()
    return set(hist[0].keys())


def main() -> None:
    daily = sorted(p.name for p in (ROOT / "daily").iterdir() if p.is_dir())
    print(f"交易日: {len(daily)} ({daily[0]} ~ {daily[-1]})")

    top_keys: Counter[str] = Counter()
    profit_null = 0
    profit_total = 0
    modes = ["pre_market", "evening", "during"]
    stock_keys: set[str] = set()
    hist_keys: set[str] = set()
    board_sections: set[str] = set()
    ths_rank_keys: set[str] = set()
    during_count = 0

    for d in daily:
        for mode in modes:
            if mode == "during":
                raw_dir = ROOT / "daily" / d / "raw"
                if not raw_dir.is_dir():
                    continue
                files = list(raw_dir.glob("during*.json"))
                if not files:
                    continue
                data = load(files[len(files) // 2])  # mid snapshot
                during_count += 1
            else:
                fp = ROOT / "daily" / d / "raw" / (
                    "evening.json" if mode == "evening" else "pre_market.json"
                )
                data = load(fp)
            if not isinstance(data, dict):
                continue
            inner = data.get("data", data)
            for k in inner.keys():
                top_keys[f"{mode}:{k}"] += 1
            if mode in ("pre_market", "evening", "during"):
                pe = inner.get("赚钱效应")
                profit_total += 1
                if pe is None or pe == {}:
                    profit_null += 1
            for sec in ("概念板块", "行业板块"):
                block = inner.get(sec)
                if isinstance(block, dict):
                    board_sections.update(f"{sec}/{k}" for k in block.keys())
            stock_keys |= sample_stock_keys(inner)
            for bucket in ("自选股", "同花顺人气榜"):
                rows = inner.get(bucket) or []
                if rows:
                    hist_keys |= hist_bar_fields(rows[0])
            for k in ("创新高", "持续上涨", "持续放量", "量价齐升"):
                rows = inner.get(k) or []
                if rows and isinstance(rows[0], dict):
                    ths_rank_keys.update(rows[0].keys())

    print(f"\n=== 顶层字段出现次数（按模式前缀）===")
    for k, c in sorted(top_keys.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {k}: {c}/{len(daily)}")

    print(f"\n=== 赚钱效应 null/空 比例 ===")
    print(f"  采样 {profit_total} 个快照, null/空 {profit_null} ({profit_null/max(profit_total,1)*100:.1f}%)")

    print(f"\n=== 板块子结构 ===")
    for s in sorted(board_sections):
        print(f"  {s}")

    print(f"\n=== 个股 enrich 常见字段 ({len(stock_keys)}) ===")
    for k in sorted(stock_keys):
        print(f"  {k}")

    print(f"\n=== 历史行情 K 线字段 ===")
    print(f"  {sorted(hist_keys)}")

    print(f"\n=== 形态榜字段 ===")
    print(f"  {sorted(ths_rank_keys)}")

    # state files
    print(f"\n=== state/ 文件 ===")
    state = ROOT / "state"
    for f in sorted(state.glob("*")):
        print(f"  {f.name}")

    ct = load(ROOT / "state" / "concept_tracker.json")
    if isinstance(ct, dict):
        days = list((ct.get("daily") or {}).keys())
        print(f"\n=== concept_tracker ===")
        print(f"  覆盖日期: {len(days)} ({days[0] if days else '-'} ~ {days[-1] if days else '-'})")
        sample_day = days[-1] if days else None
        if sample_day:
            day = ct["daily"][sample_day]
            print(f"  结构: {list(day.keys())}")
            for sec in ("concept", "industry"):
                if sec in day:
                    for bk in day[sec]:
                        print(f"    {sec}.{bk}: {len(day[sec][bk])} rows")

    # derived
    print(f"\n=== derived/ 文件类型 ===")
    derived_types: Counter[str] = Counter()
    for d in daily:
        der = ROOT / "daily" / d / "derived"
        if der.is_dir():
            for f in der.glob("*.json"):
                derived_types[f.name] += 1
    for name, c in derived_types.most_common():
        print(f"  {name}: {c}/{len(daily)} 日")

    # check one enriched stock deeply
    for d in reversed(daily):
        ev = load(ROOT / "daily" / d / "raw" / "evening.json")
        if not ev:
            continue
        inner = ev.get("data", ev)
        rows = inner.get("自选股") or inner.get("同花顺人气榜") or []
        if not rows:
            continue
        s = rows[0]
        print(f"\n=== 样本个股 deep ({d}, {s.get('股票代码')}) ===")
        for k in sorted(s.keys()):
            v = s[k]
            if isinstance(v, dict):
                print(f"  {k}: dict keys={list(v.keys())[:15]}")
            elif isinstance(v, list):
                print(f"  {k}: list len={len(v)}")
            else:
                print(f"  {k}: {type(v).__name__}")
        if s.get("历史行情"):
            print(f"  历史行情 bars: {len(s['历史行情'])}")
            if len(s["历史行情"]) >= 2:
                print(f"  末2根: {s['历史行情'][-2:]}")
        break

    # missing checks for ideal strategy
    IDEAL_NEEDS = {
        "大盘指数": "环境档位",
        "赚钱效应": "涨跌家数/涨停",
        "涨停概况/涨停统计": "连板高度",
        "概念板块": "概念四榜",
        "行业板块": "行业四榜",
        "大盘资金流": "指数资金",
        "自选股+历史行情": "MA/回踩",
        "盘口/分时": "企稳/三确认",
        "所属概念/概念粘合度": "主线共振",
        "个股资金流": "资金确认",
        "concept_tracker.json": "板块多日窗口",
        "derived/scores_watchlist.json": "候选评分",
        "trades/executed.json": "成交回放",
    }
    print(f"\n=== 理想策略数据项 vs 本地 ===")
    checks = {
        "大盘指数": top_keys.get("evening:大盘指数", 0) > 0,
        "赚钱效应(evening)": top_keys.get("evening:赚钱效应", 0) > 0,
        "赚钱效应(pre_market)": top_keys.get("pre_market:赚钱效应", 0) > 0,
        "涨停概况(pre)": top_keys.get("pre_market:涨停概况", 0) > 0,
        "涨停统计(evening)": top_keys.get("evening:涨停统计", 0) > 0,
        "概念板块": "概念板块/涨幅榜" in board_sections or any("概念板块" in x for x in board_sections),
        "行业板块": any("行业板块" in x for x in board_sections),
        "大盘资金流": top_keys.get("evening:大盘资金流", 0) > 0,
        "历史行情OHLC": bool({"最高", "最低", "收盘"} & hist_keys or {"high", "low", "close"} & hist_keys),
        "历史成交量": bool({"成交量", "成交额"} & hist_keys),
        "concept_tracker": ct is not None,
        "during快照": during_count > 0,
        "形态榜四键": all(top_keys.get(f"evening:{k}", 0) > 0 for k in ("创新高", "持续上涨", "持续放量", "量价齐升")),
    }
    for k, ok in checks.items():
        print(f"  {'✅' if ok else '❌'} {k}")


if __name__ == "__main__":
    main()
