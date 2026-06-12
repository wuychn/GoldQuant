"""回测引擎：按日重放 daily/raw 盘中快照。"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from quant.backtest.broker import BrokerConfig, SimBroker
from quant.backtest.metrics import compute_metrics
from quant.scoring.context import ScoreContext
from quant.signals.buy import generate_buy_signals
from quant.signals.models import TradeSignal
from quant.signals.sell import generate_sell_signals
from quant.store.paths import QUANT_HOME


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


def _load_during_payloads(day_dir: Path) -> list[dict]:
    raw = day_dir / "raw"
    if not raw.is_dir():
        return []
    files = sorted(raw.glob("during*.json"))
    out: list[dict] = []
    for fp in files:
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        data = obj.get("data") if isinstance(obj, dict) else obj
        if isinstance(data, dict):
            out.append(data)
    return out


def _stock_map(payload: dict) -> dict[str, dict]:
    m: dict[str, dict] = {}
    for key in ("自选股", "持仓股"):
        for row in payload.get(key) or []:
            if isinstance(row, dict):
                code = str(row.get("股票代码", "")).strip()
                if code:
                    m[code] = row
    return m


@contextmanager
def _patch_broker_state(broker: SimBroker) -> Iterator[None]:
    def _codes_sold_today(date_str: str | None = None) -> set[str]:
        del date_str
        return set(broker.sold_today)

    def _holding_codes_bought_today(holdings=None) -> set[str]:
        del holdings
        return set(broker.bought_today)

    with (
        patch("quant.signals.buy.get_holdings", broker.holdings_rows),
        patch("quant.signals.sell.get_holdings", broker.holdings_rows),
        patch("quant.gates.rules.get_holdings", broker.holdings_rows),
        patch("quant.gates.rules.get_cash", lambda: broker.cash),
        patch("quant.store.state.get_holdings", broker.holdings_rows),
        patch("quant.store.state.get_cash", lambda: broker.cash),
        patch("quant.signals.pipeline.codes_sold_today", _codes_sold_today),
        patch("quant.signals.pipeline.holding_codes_bought_today", _holding_codes_bought_today),
        patch("quant.gates.rules.codes_sold_today", _codes_sold_today),
    ):
        yield


def run_backtest(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    broker_cfg: BrokerConfig | None = None,
) -> dict:
    """重放 daily/raw 下 during*.json；跳过三确认，直接执行原始信号。"""
    broker = SimBroker(cfg=broker_cfg or BrokerConfig())
    dates = _list_trading_dates(from_date, to_date)
    days_run = 0

    for d in dates:
        payloads = _load_during_payloads(QUANT_HOME / "daily" / d)
        if not payloads:
            continue
        broker.reset_daily(d)
        days_run += 1
        last_payload = payloads[-1]

        with _patch_broker_state(broker):
            for payload in payloads:
                payload = dict(payload)
                payload["持仓股"] = broker.holdings_rows()
                ctx = ScoreContext.from_payload(payload, mode="during_market")
                stock_by_code = _stock_map(payload)

                raw_sell = generate_sell_signals(ctx)
                for sig in raw_sell:
                    broker.try_sell(sig, stock_by_code.get(sig.code, {}))

                raw_buy = generate_buy_signals(ctx, mode="during_market")
                for sig in raw_buy:
                    broker.try_buy(sig, stock_by_code.get(sig.code, {}))

        broker.mark_to_market(last_payload)

    metrics = compute_metrics(broker)
    metrics["days_run"] = days_run
    metrics["date_from"] = dates[0] if dates else ""
    metrics["date_to"] = dates[-1] if dates else ""
    return metrics
