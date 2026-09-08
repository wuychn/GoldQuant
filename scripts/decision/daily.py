"""日频决策：选股 → 决策卡 → 纸面模拟买卖 → 飞书推送。

默认走 ``momentum_swing``（昨日强势 + 沪深300 门控 + 双槽）。
``quant.yml`` 里 ``momentum_swing.enabled: false`` 时回退 IC + SwapGate。

用法：
    python -m scripts.decision.daily
    python -m scripts.decision.daily --dry-run
    python -m scripts.decision.daily --no-push
    python -m scripts.decision.daily --no-paper
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from quant.config import load_factor_weights_info
from quant.data.adjust import load_adjusted_daily
from quant.data.calendar import is_trading_day, next_trading_day, to_iso, trading_day_list, trading_days_between
from quant.data.industry import read_industry_snapshot
from quant.data.universe import universe_codes
from quant.decision.daily_output import DecisionCard, build_decision_card, card_to_text
from quant.decision.paper_execute import (
    paper_home_context,
    paper_summary_text,
    write_battle_pool,
    write_sell_watch,
)
from quant.exit.atr import atr
from quant.exit.rules import evaluate_exits
from quant.exit.state import ExitTracker
from quant.factors.compose import alpha_attribution, compose_alpha
from quant.narrative.factor_phrases import attribution_summary
from quant.factors.panel_builder import build_panel
from quant.journal.deviation import DeviationJournal
from quant.journal.funnel import FunnelTracker
from quant.ops.modes import build_decision_push_body
from quant.ops.push import push_text
from quant.portfolio.target import TargetPortfolio
from quant.store.paths import reports_dir
from scripts.cli_home import add_home_argument, home_context
from quant.store.state import get_account, get_holdings, update_holding_exit_meta
from common.progress_log import log_progress, log_progress_done, log_progress_error, log_progress_start
from common.timeutil import cn_now

_SCOPE = "decision.daily"


def _prev_trading_day(d: date) -> date | None:
    cur = d - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(cur):
            return cur
        cur -= timedelta(days=1)
    return None


def _resolve_as_of(date_arg: str | None) -> str:
    today = date.fromisoformat(date_arg) if date_arg else cn_now().date()
    if not is_trading_day(today):
        prev = _prev_trading_day(today)
        if prev is None:
            raise SystemExit("找不到交易日")
        today = prev
    return today.isoformat()


def build_today_card(
    as_of: str,
    *,
    n_enter: int = 8,
    n_exit: int = 15,
    max_positions: int = 10,
    use_paper_holdings: bool = True,
) -> tuple[DecisionCard, object, dict[str, float], dict[str, str], list, dict[str, float]]:
    today = date.fromisoformat(as_of)
    start = today - timedelta(days=400)
    hist_dates = [to_iso(d) for d in trading_day_list(start, today)]
    # KEYSTONE：出场 ATR/MA20、目标组合 realized_vol 与因子 alpha 同在后复权基准
    daily = load_adjusted_daily(end=as_of)
    uni = universe_codes(as_of)
    print(f"决策日 {as_of} | universe={len(uni)}")

    panel_dates = hist_dates[-5:] if len(hist_dates) > 5 else hist_dates
    log_progress(_SCOPE, "构建因子面板 …", detail=f"{len(panel_dates)} 日")
    panel = build_panel(panel_dates, daily=daily)
    rows_today = [r for r in panel if r.date == as_of]
    if not rows_today:
        if panel:
            last = max(r.date for r in panel)
            rows_today = [r for r in panel if r.date == last]
            print(f"[WARN] {as_of} 无面板，回退到 {last}")
        else:
            raise SystemExit("面板为空，检查离线库")

    # IC 驱动权重（walk-forward OOS）；无 factor_weights_ts.yml 时回退 registry 默认
    w_info = load_factor_weights_info(as_of=as_of)
    factor_weights = w_info.get("weights") if w_info else None
    if w_info:
        print(f"因子权重: source={w_info.get('source')} meta={w_info.get('meta')}")
    else:
        print("[WARN] walk-forward 权重缺失（strict OOS），回退 registry 默认权重")
    alpha = compose_alpha(rows_today, weights=factor_weights)
    attribution = alpha_attribution(rows_today, weights=factor_weights)
    sectors = read_industry_snapshot(as_of)
    policy = TargetPortfolio.from_config(
        n_enter=n_enter,
        n_exit=n_exit,
        max_stocks=max_positions,
        daily=daily,
        sectors=sectors,
    )
    if use_paper_holdings:
        with paper_home_context():
            holdings = get_holdings()
    else:
        holdings = get_holdings()

    buy_dates: dict[str, str] = {}
    for h in holdings:
        code = str(h.get("股票代码") or "").strip()
        bd = str(h.get("买入时间") or "")[:10]
        if code and bd:
            buy_dates[code] = bd
    policy.holding_buy_dates = buy_dates

    prices: dict[str, float] = {}
    names: dict[str, str] = {}
    day = daily[daily["date"].astype(str).str.slice(0, 10) == as_of]
    for _, r in day.iterrows():
        code = str(r["code"])
        prices[code] = float(r["close"])
        names[code] = str(r.get("name") or code)

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

    def _cal(a: str, b: str) -> int:
        from datetime import date as _d
        return trading_days_between(_d.fromisoformat(a), _d.fromisoformat(b))

    from quant.backtest.engine import ExitConfig

    exit_cfg = ExitConfig.from_quant_yml()
    exit_cfg.calendar_fn = _cal

    exit_signals: list[dict] = []
    tracker = ExitTracker()
    for h in holdings:
        code = str(h.get("股票代码") or "").strip()
        if not code or code not in prices:
            continue
        try:
            entry_raw = float(h.get("买入价") or h.get("成本价") or 0)
        except (TypeError, ValueError):
            entry_raw = 0.0
        buy_date = str(h.get("买入时间") or "")[:10] or as_of
        if entry_raw <= 0:
            continue
        hist = daily[(daily["code"] == code) & (daily["date"] <= as_of)].sort_values("date")
        if hist.empty:
            continue
        # entry 是 raw 买入价，而 prices/hist 是后复权；用 buy_date 的 hfq close 作 entry
        # 的复权近似（买入价≈当日 close），消除 "raw entry vs hfq close" 除权累积偏差（P0）
        buy_row = hist[hist["date"] == buy_date]
        entry = float(buy_row["close"].iloc[0]) if not buy_row.empty else entry_raw
        # highest_close 缺失回退 entry（与回测 ExitTracker 同源），而非全历史 close.max()（P1）
        highest = float(h.get("持仓最高价") or entry)
        tracker.upsert(code, entry, buy_date)
        tracker.update(code, prices[code])
        st = tracker.get(code)
        sig = evaluate_exits(
            hist,
            entry_price=entry,
            highest_close=st.highest_close if st else highest,
            buy_date=buy_date,
            as_of=as_of,
            atr_mult=exit_cfg.atr_mult,
            atr_mult_stop=exit_cfg.atr_mult_stop,
            hard_pct=exit_cfg.hard_pct,
            max_hold_days=exit_cfg.max_hold_days,
            calendar_fn=exit_cfg.calendar_fn,
            atr_trailing=exit_cfg.atr_trailing,
            use_trend_force=exit_cfg.use_trend_force,
            trend_fail_days=exit_cfg.trend_fail_days,
            ma_period=exit_cfg.ma_period,
        )
        if exit_cfg.atr_trailing and len(hist) >= 15:
            a = atr(hist, 14).iloc[-1]
            if a == a and a > 0:
                stop = max(entry, highest) - float(exit_cfg.atr_mult) * float(a)
                dist = (prices[code] - stop) / prices[code]
                if dist < 0.03 and sig is None:
                    exit_signals.append(
                        {"code": code, "reason": "near_atr_trailing", "price": prices[code], "stop": stop}
                    )
        if sig is not None:
            exit_signals.append({"code": code, "reason": sig.reason, "price": sig.price})
        if not use_paper_holdings:
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
    # 扩展 alpha_top 为全量排序前 50，供买入原因写 rank/α
    card.alpha_top = sorted(alpha.items(), key=lambda kv: -kv[1])[:50]
    card.weights_source = w_info.get("source") if w_info else "registry_default"
    return card, daily, prices, names, uni, alpha, attribution


def _build_sell_watch(card, daily, names, as_of, holdings) -> list[dict]:
    """对持仓算 stop 价 + force_sell，落盘供 T+1 盘中卖出监控（不撮合）。

    SwapGate 模式下硬止损可关；force_sell 以 evaluate_exits（含 trend_force）为准。
    """
    from quant.backtest.engine import ExitConfig
    from quant.exit.atr import atr

    exit_cfg = ExitConfig.from_quant_yml()
    exit_by_code = {str(s.get("code")): s for s in (card.exit_signals or [])}
    rows: list[dict] = []
    for h in holdings:
        code = str(h.get("股票代码") or "").strip()
        if not code:
            continue
        try:
            entry_raw = float(h.get("买入价") or 0)
            qty = int(float(h.get("持仓股数") or 0))
        except (TypeError, ValueError):
            continue
        buy_date = str(h.get("买入时间") or "")[:10] or as_of
        if entry_raw <= 0 or qty <= 0:
            continue
        hist = daily[(daily["code"] == code) & (daily["date"] <= as_of)].sort_values("date")
        if hist.empty:
            continue
        buy_row = hist[hist["date"] == buy_date]
        entry = float(buy_row["close"].iloc[0]) if not buy_row.empty else entry_raw
        highest = float(h.get("持仓最高价") or entry)
        a = float(atr(hist, 14).iloc[-1]) if len(hist) >= 15 else 0.0
        hard_stop = None
        if exit_cfg.hard_pct is not None:
            hard_stop = entry * (1 - float(exit_cfg.hard_pct))
            if exit_cfg.atr_mult_stop is not None and a > 0:
                hard_stop = max(hard_stop, entry - float(exit_cfg.atr_mult_stop) * a)
        atr_stop = None
        if exit_cfg.atr_trailing and a > 0:
            atr_stop = max(entry, highest) - float(exit_cfg.atr_mult) * a
        ma20 = float(hist["close"].rolling(20).mean().iloc[-1]) if len(hist) >= 20 else None
        es = exit_by_code.get(code)
        rows.append(
            {
                "code": code,
                "name": names.get(code) or code,
                "qty": qty,
                "entry": round(entry, 4),
                "highest": round(highest, 4),
                "atr": round(a, 4) if a > 0 else None,
                "hard_stop": round(hard_stop, 4) if hard_stop is not None else None,
                "atr_stop": round(atr_stop, 4) if atr_stop else None,
                "ma20": round(ma20, 4) if ma20 else None,
                "force_sell": es is not None,
                "reason": (es or {}).get("reason") or "",
            }
        )
    return rows


def _run_momentum_daily(as_of: str, *, do_paper: bool, no_push: bool, dry_run: bool, out_arg: str | None) -> None:
    from quant.swing.momentum import MomentumConfig, build_momentum_plan, save_slot_state

    cfg = MomentumConfig.from_config()
    daily = load_adjusted_daily(end=as_of)
    names: dict[str, str] = {}
    day = daily[daily["date"].astype(str).str.slice(0, 10) == as_of]
    for _, r in day.iterrows():
        code = str(r["code"])
        names[code] = str(r.get("name") or code)

    holdings: list[dict] = []
    if do_paper:
        with paper_home_context():
            holdings = get_holdings()
    plan = build_momentum_plan(as_of, daily, holdings, names, cfg=cfg)
    if do_paper:
        with paper_home_context():
            save_slot_state(plan.slot_state)
            _pool_path = write_battle_pool(plan.target_date, plan.battle_pool, extra=plan.extra)
            _sw_path = write_sell_watch(plan.target_date, plan.sell_watch)
        print(f"动量作战池({plan.target_date}): {len(plan.battle_pool)} 只 → {_pool_path}")
        print(f"卖出监控({plan.target_date}): {len(plan.sell_watch)} 只 → {_sw_path}")
        if plan.skip_reason:
            print(f"开仓跳过: {plan.skip_reason}")

    text_lines = [
        f"动量策略 {as_of} → {plan.target_date}",
        f"HS300 {plan.hs300} MA{cfg.ma}={plan.hs300_ma} gate={'ON' if plan.gate_on else 'OFF'} "
        f"force={plan.force} idle={plan.idle}",
        f"slot={plan.slot_id} scale={plan.slot_scale} skip={plan.skip_reason or '-'}",
        "候选: " + ", ".join(f"{p['name']}({p['code']}) {p['alpha']:.2%}" for p in plan.battle_pool[:8]),
    ]
    text = "\n".join(text_lines)
    print(text)

    out_dir = Path(out_arg) if out_arg else reports_dir("decision")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"decision_{as_of}.txt").write_text(text, encoding="utf-8")

    payload: dict = {
        "date": as_of,
        "strategy": "momentum",
        "weights_source": "momentum_ret1",
        "target": {p["code"]: p["target_weight"] for p in plan.battle_pool if p.get("target_weight")},
        "actions": [],
        "exit_signals": [s for s in plan.sell_watch if s.get("force_sell")],
        "funnel": {},
        "alpha_top": [(p["code"], p["alpha"]) for p in plan.battle_pool[:10]],
        "battle_pool": plan.battle_pool,
        "battle_pool_date": plan.target_date,
        "momentum": {
            "gate_on": plan.gate_on,
            "force": plan.force,
            "idle": plan.idle,
            "hs300": plan.hs300,
            "hs300_ma": plan.hs300_ma,
            "skip_reason": plan.skip_reason,
            "slot_id": plan.slot_id,
            "slot_scale": plan.slot_scale,
            "hold_days": cfg.hold_days,
            "max_idle": cfg.max_idle,
        },
        "sell_watch": plan.sell_watch,
    }
    if do_paper:
        with paper_home_context():
            _acc = get_account()
            from quant.execution.risk_gate import set_day_start_equity

            set_day_start_equity(float(_acc.get("总资产") or 0), target_date=plan.target_date)
        payload["paper"] = {
            "account": _acc,
            "holdings": [
                {
                    "code": str(h.get("股票代码")),
                    "name": h.get("股票名称"),
                    "shares": h.get("持仓股数"),
                    "cost": h.get("买入价"),
                }
                for h in holdings
            ],
        }
        (out_dir / f"paper_{as_of}.json").write_text(
            json.dumps(payload["paper"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not no_push and not dry_run:
            body = build_decision_push_body(payload)
            push_text("晚间复盘", body, mode="daily_decision", push=True)
    (out_dir / f"decision_{as_of}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"已写入 {out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description="日频决策：选股、决策卡、纸面模拟与飞书推送")
    add_home_argument(ap)
    ap.add_argument("--date", default=None, help="决策日 YYYY-MM-DD，默认今天（非交易日回退最近交易日）")
    ap.add_argument("--out", default=None, help="决策报告输出目录；默认 $QUANT_HOME/reports/decision")
    ap.add_argument("--n-enter", type=int, default=None, help="目标组合纳入阈值排名（默认读 swap_gate/8）")
    ap.add_argument("--n-exit", type=int, default=None, help="目标组合剔除阈值排名（默认读 swap_gate/15）")
    ap.add_argument("--max-positions", type=int, default=None, help="最大持仓只数（默认读 swap_gate/10）")
    ap.add_argument("--battle-pool-size", type=int, default=30, help="作战池规模（alpha top N，供 T+1 盘中择时，默认 30）")
    ap.add_argument("--no-paper", action="store_true", help="仅输出决策卡，不读写纸面账户/作战池")
    ap.add_argument("--dry-run", action="store_true", help="完整跑决策但不推飞书")
    ap.add_argument("--no-push", action="store_true", help="不推送飞书（仍写报告与纸面文件）")
    args = ap.parse_args()

    do_paper = not args.no_paper
    with home_context(args.home):
        from quant.config import load_quant_config
        from quant.portfolio.swap_gate import SwapGateConfig

        sg = SwapGateConfig.from_mapping((load_quant_config().get("portfolio") or {}).get("swap_gate"))
        n_enter = args.n_enter if args.n_enter is not None else (sg.n_enter if sg.enabled else 8)
        n_exit = args.n_exit if args.n_exit is not None else (sg.n_exit if sg.enabled else 15)
        max_positions = (
            args.max_positions
            if args.max_positions is not None
            else (sg.max_stocks if sg.enabled else 10)
        )
        as_of = _resolve_as_of(args.date)
        log_progress_start(_SCOPE, "开始", detail=f"as_of={as_of}")
        try:
            from quant.swing.momentum import momentum_enabled

            if momentum_enabled():
                _run_momentum_daily(
                    as_of,
                    do_paper=do_paper,
                    no_push=args.no_push,
                    dry_run=args.dry_run,
                    out_arg=args.out,
                )
                log_progress_done(_SCOPE, "成功", detail=f"{as_of} momentum")
                return
            card, daily, prices, names, uni, _alpha, _attribution = build_today_card(
                as_of,
                n_enter=n_enter,
                n_exit=n_exit,
                max_positions=max_positions,
                use_paper_holdings=do_paper,
            )

            text = card_to_text(card)
            print(text)

            funnel = FunnelTracker()
            funnel.observe_universe(as_of, uni)
            funnel.observe_target(as_of, list(card.target_weights.keys()))
            for a in card.actions:
                if a.side == "buy":
                    funnel.observe_buy(as_of, a.code)
            fsum = funnel.summary()
            print("\n漏斗:", fsum)

            # 作战池：优先 SwapGate/目标权重通过的标的（tw=0 的盘中不会买）
            _tgt = card.target_weights or {}
            _uncalibrated = card.weights_source not in ("walk_forward", "static")
            _alpha_note = " [默认权重，未校准]" if _uncalibrated else ""
            if _tgt:
                ordered = sorted(_tgt.items(), key=lambda kv: -kv[1])
            else:
                ordered = [
                    (c, 0.0)
                    for c, _a in sorted(_alpha.items(), key=lambda kv: -kv[1])[: args.battle_pool_size]
                ]
            battle_pool = []
            for i, (c, tw) in enumerate(ordered[: args.battle_pool_size]):
                battle_pool.append(
                    {
                        "code": c,
                        "name": names.get(c) or c,
                        "alpha": round(float(_alpha.get(c, 0.0)), 4),
                        "alpha_note": _alpha_note.strip() or None,
                        "rank": i + 1,
                        "target_weight": round(float(tw), 4),
                        "why": attribution_summary(_attribution.get(c, [])),
                    }
                )
            nxt = next_trading_day(date.fromisoformat(as_of))
            target_date = nxt.isoformat() if nxt else as_of
            if do_paper:
                with paper_home_context():
                    _pool_path = write_battle_pool(target_date, battle_pool)
                print(f"作战池({target_date}): {len(battle_pool)} 只 → {_pool_path}")

            out_dir = Path(args.out) if args.out else reports_dir("decision")
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"decision_{as_of}.txt").write_text(text, encoding="utf-8")

            payload: dict = {
                "date": as_of,
                "weights_source": card.weights_source,
                "target": card.target_weights,
                "actions": [
                    {
                        "code": a.code,
                        "name": names.get(a.code) or a.code,
                        "side": a.side,
                        "target_weight": a.target_weight,
                        "current_weight": a.current_weight,
                        "delta_weight": a.delta_weight,
                        "reason": a.reason,
                    }
                    for a in card.actions
                    if a.side != "hold"
                ],
                "exit_signals": card.exit_signals,
                "funnel": fsum,
                "alpha_top": card.alpha_top[:10],
                "battle_pool": battle_pool,
                "battle_pool_date": target_date,
            }

            if do_paper:
                # 晚间只定计划，不撮合任何交易：算持仓 stop 价 → sell_watch（T+1 盘中监控）
                with paper_home_context():
                    holdings = get_holdings()
                sell_rows = _build_sell_watch(card, daily, names, as_of, holdings)
                with paper_home_context():
                    _sw_path = write_sell_watch(target_date, sell_rows)
                print(f"卖出监控({target_date}): {len(sell_rows)} 只 → {_sw_path}")
                payload["sell_watch"] = sell_rows
                # 账户/持仓快照（只读，不撮合）
                with paper_home_context():
                    _acc = get_account()
                # 写 T+1 风控日内基准（T 晚总资产 ≈ T+1 开盘前），避免盘中首次调用把带亏总资产当基准
                with paper_home_context():
                    from quant.execution.risk_gate import set_day_start_equity

                    set_day_start_equity(float(_acc.get("总资产") or 0), target_date=target_date)
                payload["paper"] = {
                    "account": _acc,
                    "holdings": [
                        {"code": str(h.get("股票代码")), "name": h.get("股票名称"),
                         "shares": h.get("持仓股数"), "cost": h.get("买入价")}
                        for h in holdings
                    ],
                }
                (out_dir / f"paper_{as_of}.json").write_text(
                    json.dumps(payload["paper"], ensure_ascii=False, indent=2), encoding="utf-8"
                )
                if not args.no_push and not args.dry_run:
                    body = build_decision_push_body(payload)
                    push_text("晚间复盘", body, mode="daily_decision", push=True)

            (out_dir / f"decision_{as_of}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"已写入 {out_dir}")
            log_progress_done(_SCOPE, "成功", detail=as_of)
        except SystemExit as e:
            log_progress_error(_SCOPE, "失败", detail=str(e) or f"exit={e.code}")
            raise
        except Exception as e:
            log_progress_error(_SCOPE, "失败", detail=f"{type(e).__name__}: {e}")
            raise


if __name__ == "__main__":
    main()
