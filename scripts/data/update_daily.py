"""每日增量：1 次 spot_em 追加当日 + 除权检测 + 因子补拉 + 指数增量。

收盘后执行。spot_em 无历史，当天没抓就补不回来，故失败必须告警。

用法：
    python -m scripts.data.update_daily
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from quant.data.adjust import detect_ex_dividend_codes, refresh_adj_for_codes
from quant.data.calendar import is_trading_day
from quant.data.fetch import fetch_index, fetch_spot_em, fetch_trade_calendar
from quant.data.store import read_daily_raw, write_calendar, write_daily_raw, write_index_daily
from quant.timeutil import cn_now


def _latest_date(daily: pd.DataFrame) -> str | None:
    if daily.empty:
        return None
    return str(daily["date"].max())


def _prev_close_map(daily: pd.DataFrame, as_of: str) -> dict[str, float]:
    """as_of 之前最近一日的 {code: close}。"""
    if daily.empty:
        return {}
    prev = daily[daily["date"] < as_of]
    if prev.empty:
        return {}
    last = prev["date"].max()
    d = prev[prev["date"] == last]
    return dict(zip(d["code"].astype(str), pd.to_numeric(d["close"], errors="coerce")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="指定日 YYYY-MM-DD，默认今天")
    ap.add_argument("--index", default="000300", help="基准指数代码")
    args = ap.parse_args()

    today = args.date or cn_now().strftime("%Y-%m-%d")
    if not is_trading_day(__import__("datetime").date.fromisoformat(today)):
        print(f"{today} 非交易日，跳过")
        return

    # 1. spot_em 全市场当日
    try:
        spot = fetch_spot_em()
    except Exception as e:
        print(f"[FATAL] spot_em 拉取失败，当日数据将缺失且无法补回: {e}", file=sys.stderr)
        sys.exit(2)

    if spot.empty:
        print("[WARN] spot_em 返回空，跳过", file=sys.stderr)
        sys.exit(2)

    spot["date"] = today
    # 仅保留 daily_raw 列
    cols = ["code", "date", "name", "open", "high", "low", "close", "pre_close",
            "volume", "amount", "turnover_rate", "float_mv", "total_mv"]
    spot_out = spot[[c for c in cols if c in spot.columns]]
    write_daily_raw(spot_out)
    print(f"spot_em 追加: {len(spot_out)} 行 @ {today}")

    # 1b. PIT 名称快照（供 universe ST/退市过滤、涨跌停 ST 分档）
    #     spot_em 的 name 是当日真实名（PIT），落库后 universe 可按日取，避免依赖
    #     daily_raw.name（历史常缺失或为查询当下的当前名）。
    try:
        from quant.data.store import write_name_snapshot

        code_name = {
            str(r.get("code", "")).strip(): str(r.get("name", "")).strip()
            for _, r in spot.iterrows()
            if str(r.get("code", "")).strip() and str(r.get("name", "")).strip()
        }
        write_name_snapshot(today, code_name)
        print(f"name 快照: {len(code_name)} 只 @ {today}")
    except Exception as e:
        print(f"[WARN] name 快照失败: {e}", file=sys.stderr)

    # 2. 除权检测 + 因子补拉
    daily = read_daily_raw(end=today)
    prev_map = _prev_close_map(daily, today)
    ex_codes = detect_ex_dividend_codes(spot, prev_map)
    if ex_codes:
        print(f"检测到除权 {len(ex_codes)} 只，补拉复权因子: {ex_codes[:10]}{'...' if len(ex_codes)>10 else ''}")
        n = refresh_adj_for_codes(ex_codes)
        print(f"复权因子更新 {n} 条")
    else:
        print("无除权")

    # 3. 指数增量
    try:
        start = _latest_date(daily) or today
        df = fetch_index(args.index, start=start, end=today)
        write_index_daily(df)
        print(f"指数 {args.index} 增量: {len(df)} 行")
    except Exception as e:
        print(f"[WARN] 指数增量失败: {e}", file=sys.stderr)

    # 4. 交易日历刷新（低频）
    try:
        cal = fetch_trade_calendar()
        write_calendar(cal)
    except Exception:
        pass

    # 5. 行业 PIT 快照（供中性化 / 组合约束）
    try:
        from quant.data.industry import fetch_current_industry_map, write_industry_snapshot

        ind_map = fetch_current_industry_map()
        if ind_map:
            write_industry_snapshot(today, ind_map)
            print(f"行业快照: {len(ind_map)} 只 @ {today}")
        else:
            print("[WARN] 行业映射为空，跳过落库", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] 行业快照失败: {e}", file=sys.stderr)

    # 6. Universe PIT 快照
    try:
        from quant.data.universe import universe_snapshot

        snap = universe_snapshot(today, rebuild=True, daily=daily)
        n_inc = int(snap["included"].sum()) if not snap.empty and "included" in snap.columns else 0
        print(f"universe 快照: {n_inc} 只纳入 / {len(snap)} 行 @ {today}")
    except Exception as e:
        print(f"[WARN] universe 快照失败: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
