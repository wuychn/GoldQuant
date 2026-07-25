"""端到端日级回测状态机。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from quant.backtest.broker import BrokerConfig, SimBroker
from quant.backtest.engine import (
    WATCHLIST_META_KEYS,
    _make_close_provider,
    _patch_runtime_state,
    _snapshot_datetime,
    enrich_holdings_with_payload,
    index_payload_stocks,
)
from quant.execution.core import match_buy_order, match_sell_order
from quant.pool.evening_watchlist import update_watchlist_evening_core
from quant.portfolio.risk import PortfolioRiskState, check_drawdown_halt, update_peak
from quant.research.metrics.performance import compute_extended_metrics
from quant.research.state import MemoryPortfolioState
from quant.scoring.context import ScoreContext
from quant.scoring.regime import RegimeTracker, reset_regime_tracker, refresh_regime
from quant.scoring.theme_tracker import update_concept_tracker_state
from quant.signals.pipeline import generate_confirmed_signals
from quant.store.paths import QUANT_HOME
from quant.timeutil import cn_now


def _list_trading_dates(from_date: str | None, to_date: str | None) -> list[str]:
    root = QUANT_HOME / "daily"
    if not root.is_dir():
        return []
    dates = sorted(p.name for p in root.iterdir() if p.is_dir())
    if from_date:
        dates = [d for d in dates if d >= from_date]
    if to_date:
        dates = [d for d in dates if d <= to_date]
    return dates


def _load_json_payload(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(obj, dict):
        inner = obj.get("data")
        return inner if isinstance(inner, dict) else obj
    return None


def _load_during_payloads(day_dir: Path) -> list[tuple[datetime, dict]]:
    raw = day_dir / "raw"
    if not raw.is_dir():
        return []
    files = sorted(raw.glob("during*.json"))
    out: list[tuple[datetime, dict]] = []
    date_str = day_dir.name
    for fp in files:
        data = _load_json_payload(fp)
        if data:
            out.append((_snapshot_datetime(fp.name, date_str), data))
    return out


def _load_evening_payload(day_dir: Path) -> dict | None:
    raw = day_dir / "raw"
    for name in ("post_market_evening.json", "evening.json"):
        p = raw / name
        data = _load_json_payload(p)
        if data:
            return data
    return None


def _inject_state(payload: dict, mem: MemoryPortfolioState) -> dict:
    """把 mem 的自选/持仓注入 payload，同时保留快照里的 rich 行情数据。

    mem.watchlist 存的是晚间流程产出的 thin 行（仅 评分/原因/战法 等元数据），
    直接覆盖 payload['自选股'] 会让盘中买入评估拿不到 历史行情/盘口/价格，
    导致 _evaluate_buy_candidate 全部返回 None、回测零成交。
    这里改为：以快照中同代码的 rich 行为底，叠加自选元数据后注入。
    持仓同理，避免 sell.py 算止损时拿不到行情。
    """
    from app.utils.common_util import extract_stock_code

    rich_by_code = index_payload_stocks(payload)

    merged_watchlist: list[dict] = []
    for w in mem.watchlist:
        code = extract_stock_code(w)
        rich = rich_by_code.get(code) if code else None
        if rich is None:
            merged_watchlist.append(dict(w))
            continue
        base = dict(rich)
        for k in WATCHLIST_META_KEYS:
            if k in w:
                base[k] = w[k]
        base["股票代码"] = code
        if w.get("股票名称"):
            base["股票名称"] = w["股票名称"]
        merged_watchlist.append(base)

    p = dict(payload)
    p["自选股"] = merged_watchlist
    p["持仓股"] = enrich_holdings_with_payload(mem.get_holdings(), payload)
    return p


def _sync_broker_from_mem(broker: SimBroker, mem: MemoryPortfolioState) -> None:
    broker.cash = mem.cash
    broker.holdings = {str(h.get("股票代码", "")).strip(): h for h in mem.get_holdings()}
    broker.sold_today = set(mem.sold_today)
    broker.bought_today = set(mem.bought_today)


def _sync_mem_from_broker(mem: MemoryPortfolioState, broker: SimBroker) -> None:
    mem.cash = broker.cash
    mem.holdings = {str(h.get("股票代码", "")).strip(): h for h in broker.holdings_rows()}
    mem.sold_today = set(broker.sold_today)
    mem.bought_today = set(broker.bought_today)


def _execute_on_broker(
    broker: SimBroker,
    signals: list,
    stock_by_code: dict,
    *,
    enforce_hours: bool = False,
) -> None:
    sim = broker._sim
    for sig in signals:
        if sig.action == "卖出":
            h = broker.holdings.get(sig.code)
            if not h:
                continue
            mr = match_sell_order(
                sig,
                stock_by_code.get(sig.code, {}),
                holding_qty=int(h.get("持仓股数", 0) or 0),
                buy_price=float(h.get("买入价", 0) or 0),
                bought_today=sig.code in broker.bought_today,
                enforce_hours=enforce_hours,
                enforce_late_session=True,
                sim=sim,
            )
            if not mr.ok:
                from quant.backtest.broker import FillRecord

                broker.trades.append(
                    FillRecord(broker.current_date, sig, 0, 0, 0, 0, rejected=mr.rejected)
                )
                continue
            broker.try_sell(sig, stock_by_code.get(sig.code, {}))
        else:
            mr = match_buy_order(
                sig,
                stock_by_code.get(sig.code, {}),
                cash=broker.cash,
                sold_today=sig.code in broker.sold_today,
                already_held=sig.code in broker.holdings,
                enforce_hours=enforce_hours,
                sim=sim,
            )
            if not mr.ok:
                from quant.backtest.broker import FillRecord

                broker.trades.append(
                    FillRecord(broker.current_date, sig, 0, 0, 0, 0, rejected=mr.rejected)
                )
                continue
            broker.try_buy(sig, stock_by_code.get(sig.code, {}))


def _stock_map(payload: dict) -> dict[str, dict]:
    m: dict[str, dict] = {}
    for key in ("自选股", "持仓股"):
        for row in payload.get(key) or []:
            if isinstance(row, dict):
                code = str(row.get("股票代码", "")).strip()
                if code:
                    m[code] = row
    return m


def run_full_system_backtest(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    broker_cfg: BrokerConfig | None = None,
) -> dict[str, Any]:
    """端到端：晚间自选演化 + 盘中三确认成交。"""
    dates = _list_trading_dates(from_date, to_date)
    start = dates[0].replace("-", "") if dates else None
    end = dates[-1].replace("-", "") if dates else None
    price_provider = _make_close_provider(start, end) if dates else None

    broker = SimBroker(cfg=broker_cfg or BrokerConfig(), price_provider=price_provider)
    mem = MemoryPortfolioState(cash=broker.cash)
    pending_box: list[dict] = [{}]
    clock: list[datetime] = [cn_now()]
    reset_regime_tracker(RegimeTracker())
    risk_state = PortfolioRiskState(peak_equity=broker.cfg.initial_cash)
    manifest: dict[str, Any] = {"evening_ok": 0, "during_ok": 0, "skipped": []}
    days_run = 0

    for d in dates:
        day_dir = QUANT_HOME / "daily" / d
        snapshots = _load_during_payloads(day_dir)
        evening = _load_evening_payload(day_dir)

        if not evening and not snapshots:
            manifest["skipped"].append({"date": d, "reason": "无 evening/during 快照"})
            continue

        broker.reset_daily(d)
        mem.reset_daily(d)
        days_run += 1

        if snapshots:
            with _patch_runtime_state(broker, pending_box, clock):
                _sync_broker_from_mem(broker, mem)
                last_payload = snapshots[-1][1]

                for snap_dt, raw_payload in snapshots:
                    clock[0] = snap_dt
                    payload = _inject_state(raw_payload, mem)
                    payload["持仓股"] = enrich_holdings_with_payload(
                        broker.holdings_rows(), payload
                    )
                    ctx = ScoreContext.from_payload(payload, mode="during_market")
                    refresh_regime(payload, date_str=d)
                    stock_by_code = _stock_map(payload)

                    _raw_buy, _raw_sell, executable, _audit = generate_confirmed_signals(
                        ctx, mode="during_market"
                    )
                    equity = broker.cash
                    for code, h in broker.holdings.items():
                        row = stock_by_code.get(code, {})
                        from quant.scoring.tech_indicators import quote_last_price

                        px = quote_last_price(row) if row else None
                        if not px:
                            px = float(h.get("买入价", 0) or 0)
                        equity += px * int(h.get("持仓股数", 0) or 0)
                    update_peak(equity, risk_state)
                    ok, _ = check_drawdown_halt(equity, d, risk_state)
                    if not ok:
                        executable = [s for s in executable if s.action == "卖出"]

                    _execute_on_broker(
                        broker,
                        executable,
                        stock_by_code,
                        enforce_hours=True,
                    )

                _sync_mem_from_broker(mem, broker)
                broker.mark_to_market(_inject_state(last_payload, mem))
                manifest["during_ok"] += 1

        if evening:
            payload = _inject_state(evening, mem)
            update_concept_tracker_state(payload)
            ctx = ScoreContext.from_payload(payload, mode="post_market_evening")
            result = update_watchlist_evening_core(
                ctx,
                existing_watchlist=mem.watchlist,
                existing_observe=mem.observe_pool,
            )
            mem.set_watchlist(result.watchlist)
            mem.set_observe_pool(result.observe_pool)
            manifest["evening_ok"] += 1

    metrics = compute_extended_metrics(broker, trading_days=days_run)
    metrics["days_run"] = days_run
    metrics["date_from"] = dates[0] if dates else ""
    metrics["date_to"] = dates[-1] if dates else ""
    metrics["mode"] = "full_system"
    metrics["data_manifest"] = manifest
    from quant.research.attribution import attribute_pnl_by_signal_type

    metrics["attribution"] = attribute_pnl_by_signal_type(broker)
    return metrics
