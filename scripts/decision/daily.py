"""日频决策入口：universe → panel → alpha → TargetPortfolio → 决策卡。

用法：
    python -m scripts.decision.daily [--date 2024-06-28] [--out reports/decision]
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from quant.data.calendar import is_trading_day, to_iso, trading_day_list
from quant.data.industry import read_industry_snapshot
from quant.data.store import read_daily_raw
from quant.data.universe import universe_codes
from quant.decision.daily_output import build_decision_card, card_to_text
from quant.exit.atr import atr
from quant.exit.rules import evaluate_exits
from quant.exit.state import ExitTracker
from quant.factors.compose import compose_alpha
from quant.factors.panel_builder import build_panel
from quant.journal.deviation import DeviationJournal
from quant.journal.funnel import FunnelTracker
from quant.portfolio2.target import TargetPortfolio
from quant.store.state import get_holdings, update_holding_exit_meta
from quant.timeutil import cn_now


def _prev_trading_day(d: date) -> date | None:
    cur = d - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(cur):
            return cur
        cur -= timedelta(days=1)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="决策日 YYYY-MM-DD，默认最近交易日")
    ap.add_argument("--out", default="reports/decision")
    ap.add_argument("--n-enter", type=int, default=8)
    ap.add_argument("--n-exit", type=int, default=15)
    ap.add_argument("--max-positions", type=int, default=10)
    args = ap.parse_args()

    today = date.fromisoformat(args.date) if args.date else cn_now().date()
    if not is_trading_day(today):
        prev = _prev_trading_day(today)
        if prev is None:
            print("找不到交易日")
            return
        today = prev
    as_of = today.isoformat()

    start = today - timedelta(days=400)
    hist_dates = [to_iso(d) for d in trading_day_list(start, today)]
    daily = read_daily_raw(end=as_of)
    uni = universe_codes(as_of)
    print(f"决策日 {as_of} | universe={len(uni)}")

    panel_dates = hist_dates[-5:] if len(hist_dates) > 5 else hist_dates
    panel = build_panel(panel_dates, daily=daily)
    rows_today = [r for r in panel if r.date == as_of]
    if not rows_today:
        if panel:
            last = max(r.date for r in panel)
            rows_today = [r for r in panel if r.date == last]
            print(f"[WARN] {as_of} 无面板，回退到 {last}")
        else:
            print("面板为空，检查离线库")
            return

    alpha = compose_alpha(rows_today)
    sectors = read_industry_snapshot(as_of)
    policy = TargetPortfolio(
        n_enter=args.n_enter,
        n_exit=args.n_exit,
        max_stocks=args.max_positions,
        daily=daily,
        sectors=sectors,
    )

    holdings = get_holdings()
    prices: dict[str, float] = {}
    day = daily[daily["date"] == as_of]
    for _, r in day.iterrows():
        prices[str(r["code"])] = float(r["close"])

    total = 0.0
    cur_val: dict[str, float] = {}
    for h in holdings:
        code = str(h.get("股票代码") or "").strip()
        try:
            qty = float(h.get("持仓股数") or h.get("股数") or 0)
            px = prices.get(code) or float(h.get("买入价") or 0)
        except (TypeError, ValueError):
            continue
        if code and qty > 0 and px > 0:
            cur_val[code] = qty * px
            total += qty * px
    current = {c: v / total for c, v in cur_val.items()} if total > 0 else {}

    target = policy.target_weights(alpha, prices, current, as_of)

    exit_signals: list[dict] = []
    tracker = ExitTracker()
    for h in holdings:
        code = str(h.get("股票代码") or "").strip()
        if not code or code not in prices:
            continue
        try:
            entry = float(h.get("买入价") or h.get("成本价") or 0)
        except (TypeError, ValueError):
            entry = 0.0
        buy_date = str(h.get("买入时间") or "")[:10]
        if entry <= 0:
            continue
        hist = daily[(daily["code"] == code) & (daily["date"] <= as_of)].sort_values("date")
        if hist.empty:
            continue
        highest = float(h.get("持仓最高价") or hist["close"].max())
        tracker.upsert(code, entry, buy_date or as_of)
        tracker.update(code, prices[code])
        st = tracker.get(code)
        sig = evaluate_exits(
            hist,
            entry_price=entry,
            highest_close=st.highest_close if st else highest,
            buy_date=buy_date or as_of,
            as_of=as_of,
        )
        if len(hist) >= 15:
            a = atr(hist, 14).iloc[-1]
            if a == a and a > 0:
                stop = max(entry, highest) - 3.0 * float(a)
                dist = (prices[code] - stop) / prices[code]
                if dist < 0.03 and sig is None:
                    exit_signals.append(
                        {
                            "code": code,
                            "reason": "near_atr_trailing",
                            "price": prices[code],
                            "stop": stop,
                        }
                    )
        if sig is not None:
            exit_signals.append({"code": code, "reason": sig.reason, "price": sig.price})

        try:
            ranked = sorted(alpha, key=lambda c: -alpha[c])
            rank = ranked.index(code) + 1 if code in alpha else 0
            a14 = float(atr(hist, 14).iloc[-1]) if len(hist) >= 15 else 0.0
            update_holding_exit_meta(
                code,
                highest_close=max(highest, prices[code]),
                buy_atr=a14 if a14 == a14 else None,
                buy_rank=rank or None,
            )
        except Exception:
            pass

    card = build_decision_card(as_of, alpha, target, current, exit_signals)
    text = card_to_text(card)
    print(text)

    funnel = FunnelTracker()
    funnel.observe_universe(as_of, uni)
    funnel.observe_target(as_of, list(target.keys()))
    for a in card.actions:
        if a.side == "buy":
            funnel.observe_buy(as_of, a.code)
    fsum = funnel.summary()
    print("\n漏斗:", fsum)
    if fsum.get("n_target", 0) == 0 and fsum.get("n_universe", 0) > 0:
        print("[ALERT] 目标组合为空：约束/因子覆盖异常")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"decision_{as_of}.txt").write_text(text, encoding="utf-8")
    payload = {
        "date": as_of,
        "target": target,
        "current": current,
        "actions": [
            {
                "code": a.code,
                "side": a.side,
                "target_weight": a.target_weight,
                "current_weight": a.current_weight,
                "delta_weight": a.delta_weight,
                "reason": a.reason,
            }
            for a in card.actions
            if a.side != "hold"
        ],
        "exit_signals": exit_signals,
        "funnel": fsum,
        "alpha_top": card.alpha_top,
    }
    (out_dir / f"decision_{as_of}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    journal = DeviationJournal(path=out_dir / "deviation.jsonl")
    journal.record_from_card(card, {a.code: (a.side, a.current_weight) for a in card.actions})
    journal.save()
    print(f"已写入 {out_dir}")


if __name__ == "__main__":
    main()
