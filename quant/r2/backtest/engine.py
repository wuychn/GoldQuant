"""R2 历史回放。"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from quant.backtest.broker import BrokerConfig, SimBroker
from quant.backtest.metrics import compute_metrics
from quant.r2.io.state import inject_combat_watchlist, reset_pools
from quant.r2.market.regime import detect_regime
from quant.r2.pool.manager import run_evening_pool_update
from quant.r2.sector.engine import build_sector_rows
from quant.r2.signals.pipeline import generate_confirmed_signals
from quant.scoring.context import ScoreContext
from quant.store.paths import QUANT_HOME
from quant.timeutil import CN_TZ, cn_now


def _list_dates(from_date: str | None, to_date: str | None) -> list[str]:
    root = QUANT_HOME / "daily"
    if not root.is_dir():
        return []
    dates = sorted(p.name for p in root.iterdir() if p.is_dir())
    if from_date:
        dates = [d for d in dates if d >= from_date]
    if to_date:
        dates = [d for d in dates if d <= to_date]
    return dates


def _load_json(path: Path) -> dict | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(obj, dict):
        inner = obj.get("data")
        return inner if isinstance(inner, dict) else obj
    return None


def _during_snapshots(day_dir: Path) -> list[tuple[datetime, dict]]:
    raw = day_dir / "raw"
    if not raw.is_dir():
        return []
    out: list[tuple[datetime, dict]] = []
    date_str = day_dir.name
    for fp in sorted(raw.glob("during*.json")):
        payload = _load_json(fp)
        if not payload:
            continue
        m = re.search(r"(\d{4})", fp.name)
        hh, mm = (int(m.group(1)[:2]), int(m.group(1)[2:])) if m else (15, 0)
        y, mo, d = (int(x) for x in date_str.split("-"))
        out.append((datetime(y, mo, d, hh, mm, tzinfo=CN_TZ), payload))
    return out


@contextmanager
def _patch_broker(broker: SimBroker, pending_box: list[dict], clock: list[datetime]) -> Iterator[None]:
    import quant.trading.confirmation as confirmation

    def _sold(_date_str: str | None = None) -> set[str]:
        return set(broker.sold_today)

    def _bought(holdings=None, today=None) -> set[str]:
        return set(broker.bought_today)

    with (
        patch("quant.store.state.get_holdings", broker.holdings_rows),
        patch("quant.store.state.get_cash", lambda: broker.cash),
        patch("quant.store.state.codes_sold_today", _sold),
        patch("quant.store.state.holding_codes_bought_today", _bought),
        patch("quant.r2.signals.pipeline.codes_sold_today", _sold),
        patch("quant.r2.signals.pipeline.holding_codes_bought_today", _bought),
        patch.object(confirmation, "_now", lambda: clock[0]),
        patch.object(confirmation, "load_pending", lambda: dict(pending_box[0])),
        patch.object(confirmation, "save_pending", lambda p: pending_box.__setitem__(0, dict(p))),
    ):
        yield


def run_r2_backtest(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    broker_cfg: BrokerConfig | None = None,
    ml_mode: str = "gate",
) -> dict:
    dates = _list_dates(from_date, to_date)
    broker = SimBroker(cfg=broker_cfg or BrokerConfig())
    pending_box: list[dict] = [{}]
    clock: list[datetime] = [cn_now()]
    regime_stats: dict[str, dict] = {}
    day_regime: dict[str, str] = {}

    from quant.r2.config import load_r2_config
    import copy

    cfg = copy.deepcopy(load_r2_config())
    cfg.setdefault("ml", {})["mode"] = ml_mode
    reset_pools()

    cfg_patch = patch("quant.r2.config.load_r2_config", lambda: cfg)
    entry_patch = patch("quant.r2.signals.entry.load_r2_config", lambda: cfg)
    ml_patch = patch("quant.r2.ml.runtime.load_r2_config", lambda: cfg)

    with cfg_patch, entry_patch, ml_patch:
        for d in dates:
            day_dir = QUANT_HOME / "daily" / d
            evening = _load_json(day_dir / "raw" / "evening.json")
            if evening:
                regime = detect_regime(evening)
                sectors = build_sector_rows(evening)
                run_evening_pool_update(evening, regime=regime, sectors=sectors, date_str=d)

            snapshots = _during_snapshots(day_dir)
            if not snapshots:
                continue
            broker.reset_daily(d)

            with _patch_broker(broker, pending_box, clock):
                for snap_dt, payload in snapshots:
                    clock[0] = snap_dt
                    payload = inject_combat_watchlist(payload, date_str=d)
                    payload["持仓股"] = broker.holdings_rows()
                    ctx = ScoreContext.from_payload(payload, mode="during_market")
                    regime = detect_regime(payload)
                    _rb, _rs, executable, _audit, _reg = generate_confirmed_signals(
                        ctx, mode="during_market", regime=regime
                    )
                    stock_map = {
                        str(r.get("股票代码", "")).strip(): r
                        for r in payload.get("自选股", []) + payload.get("持仓股", [])
                        if isinstance(r, dict)
                    }
                    for sig in executable:
                        if sig.action == "卖出":
                            broker.try_sell(sig, stock_map.get(sig.code, {}))
                        else:
                            broker.try_buy(sig, stock_map.get(sig.code, {}))

            broker.mark_to_market(snapshots[-1][1])
            reg = detect_regime(snapshots[-1][1]).regime.value
            day_regime[d] = reg
            bucket = regime_stats.setdefault(reg, {"days": 0, "sells": 0, "wins": 0, "pnl": 0.0})
            bucket["days"] += 1

    for t in broker.trades:
        if t.rejected or t.quantity <= 0 or t.signal.action != "卖出":
            continue
        reg = day_regime.get(t.date, "震荡")
        bucket = regime_stats.setdefault(reg, {"days": 0, "sells": 0, "wins": 0, "pnl": 0.0})
        bucket["sells"] += 1
        bucket["pnl"] += t.pnl
        if t.pnl > 0:
            bucket["wins"] += 1

    metrics = compute_metrics(broker)
    metrics["by_regime"] = regime_stats
    metrics["days_run"] = len(dates)
    metrics["ml_mode"] = ml_mode
    return metrics
