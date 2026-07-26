"""纸面撮合单元测试（临时目录，不碰真实账户）。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from quant.decision.daily_output import build_decision_card
from quant.decision.paper_execute import actions_to_signals, execute_decision_card
from quant.store.paths import override_quant_home


def _daily(as_of: str = "2024-06-28") -> pd.DataFrame:
    rows = []
    for i, code in enumerate(["600000", "600001", "600002"]):
        p = 10.0 + i
        rows.append(
            {
                "code": code,
                "date": as_of,
                "name": f"股{code[-3:]}",
                "open": p,
                "high": p * 1.01,
                "low": p * 0.99,
                "close": p,
                "pre_close": p,
                "volume": 1e6,
                "amount": p * 1e6,
            }
        )
    return pd.DataFrame(rows)


def test_actions_to_signals_buy_and_sell():
    alpha = {"600000": 1.0, "600001": 0.5, "600002": 0.1}
    target = {"600000": 0.5, "600001": 0.4}
    current = {"600002": 0.3}
    card = build_decision_card("2024-06-28", alpha, target, current)
    holdings = [{"股票代码": "600002", "持仓股数": 1000, "买入价": 10.0}]
    prices = {"600000": 10.0, "600001": 11.0, "600002": 12.0}
    names = {c: c for c in prices}
    sigs = actions_to_signals(
        card, prices=prices, names=names, total_assets=100_000, holdings=holdings
    )
    actions = {s.code: s.action for s in sigs}
    assert actions.get("600002") == "卖出"
    assert actions.get("600000") == "买入"
    assert all(s.quantity % 100 == 0 for s in sigs)


def test_execute_decision_card_writes_paper_account():
    as_of = "2024-06-28"
    daily = _daily(as_of)
    alpha = {"600000": 1.0, "600001": 0.8}
    target = {"600000": 0.5, "600001": 0.4}
    card = build_decision_card(as_of, alpha, target, {})

    with tempfile.TemporaryDirectory(prefix="gq-paper-") as td:
        home = Path(td)
        with override_quant_home(home):
            result = execute_decision_card(
                card,
                daily=daily,
                as_of=as_of,
                prices={"600000": 10.0, "600001": 11.0, "600002": 12.0},
                names={"600000": "A", "600001": "B", "600002": "C"},
                dry_run=False,
            )
            assert result["n_executed"] >= 1
            paper_root = home / "paper_account"
            assert (paper_root / "state" / "holding.jsonl").is_file()
            acc = result["account"]
            assert float(acc.get("总资产") or 0) > 0
            eq = paper_root / "state" / "equity.jsonl"
            assert eq.is_file()
            line = eq.read_text(encoding="utf-8").strip().splitlines()[-1]
            assert json.loads(line)["date"] == as_of


def test_dry_run_no_state_write():
    as_of = "2024-06-28"
    daily = _daily(as_of)
    card = build_decision_card(as_of, {"600000": 1.0}, {"600000": 0.5}, {})
    with tempfile.TemporaryDirectory(prefix="gq-paper-dry-") as td:
        home = Path(td)
        with override_quant_home(home):
            result = execute_decision_card(
                card,
                daily=daily,
                as_of=as_of,
                prices={"600000": 10.0},
                names={"600000": "A"},
                dry_run=True,
            )
            assert result["dry_run"] is True
            assert result["n_signals"] >= 1
            holding = home / "paper_account" / "state" / "holding.jsonl"
            assert not holding.is_file() or holding.read_text(encoding="utf-8").strip() == ""
