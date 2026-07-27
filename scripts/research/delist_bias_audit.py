"""幸存者偏差体检（Phase 4）：量化退市股对策略的影响。

回答：历史回看里，因子/作战池选中过多少「后来退市」的票？这是幸存者偏差的
直接证据——若策略曾在退市前选中它们，则剔除它们的回测必然高估绩效。

用法::

    python -m scripts.research.delist_bias_audit --as-of 2024-06-30
    python -m scripts.research.delist_bias_audit --as-of 2024-06-30 --refresh-delisted

依赖已 build_daily（含退市股）的离线库。
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import to_iso, trading_day_list
from quant.data.delist import fetch_delisted_codes
from quant.data.store import read_daily_raw
from quant.factors.compose import compose_alpha
from quant.factors.panel_builder import build_panel


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True, help="评估日 YYYY-MM-DD")
    ap.add_argument("--lookback-days", type=int, default=400)
    ap.add_argument("--top-n", type=int, default=30, help="作战池规模")
    ap.add_argument("--refresh-delisted", action="store_true", help="重新拉退市清单")
    args = ap.parse_args()

    as_of = args.as_of
    start = date.fromisoformat(as_of) - timedelta(days=args.lookback_days)
    dates = [to_iso(d) for d in trading_day_list(start, date.fromisoformat(as_of))]
    if not dates:
        print("无交易日")
        return

    # 退市清单（代码集合）
    if args.refresh_delisted:
        delisted = fetch_delisted_codes()
        del_codes = set(delisted["code"].astype(str)) if not delisted.empty else set()
    else:
        # 已入库的代码但不在当前 spot → 视为退市/停牌（近似）
        from quant.data.fetch import fetch_spot_em

        try:
            live = set(fetch_spot_em()["code"].astype(str))
        except Exception:
            live = set()
        stored = set(read_daily_raw()["code"].astype(str))
        del_codes = stored - live if live else set()
    print(f"退市/非在市代码数: {len(del_codes)}")

    daily = load_adjusted_daily()
    panel = build_panel(dates, daily=daily)
    # 取评估日面板
    rows_today = [r for r in panel if r.date == as_of]
    if not rows_today:
        print(f"{as_of} 无面板行")
        return
    alpha = compose_alpha(rows_today)
    ranked = sorted(alpha.items(), key=lambda kv: -kv[1])
    top = [c for c, _ in ranked[: args.top_n]]
    hits = [c for c in top if c in del_codes]
    print(f"作战池 top{args.top_n} 中退市/非在市票: {len(hits)} ({len(hits)/max(len(top),1):.1%})")
    for c in hits[:20]:
        print(f"  {c} α={alpha[c]:.3f}")
    if hits:
        print("\n⚠️ 策略曾在退市前选中这些票 → 当前(剔除它们的)回测高估绩效。")
    else:
        print("\n本评估日未命中退市票（仍建议多日扫描）。")


if __name__ == "__main__":
    main()
