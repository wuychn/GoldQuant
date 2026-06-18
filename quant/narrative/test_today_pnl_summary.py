"""当日盈亏摘要。"""

from __future__ import annotations

from quant.narrative.history_context import format_today_pnl_summary


def test_pnl_summary_buy_only_shows_daily_pnl(monkeypatch) -> None:
    monkeypatch.setattr(
        "quant.store.state.get_account",
        lambda: {
            "可用资金": 80000.0,
            "持仓市值": 0.0,
            "总资产": 80000.0,
            "当日已实现盈亏": 0.0,
        },
    )
    payload = {
        "持仓股": [
            {
                "股票代码": "600519",
                "股票名称": "贵州茅台",
                "持仓股数": 100,
                "买入价": 100.0,
                "盘口": {"最新": 105.0, "涨幅": 5.0},
            }
        ]
    }
    text = format_today_pnl_summary(payload, date_str="2099-01-01", sync_account=False)
    assert "当日盈亏：" in text
    assert "持仓较昨收+525.00元" in text
    assert "当日盈亏：+525.00元" in text
    assert "可用80000.00元" in text
    assert "持仓市值10500.00元" in text
    assert "总资产90500.00元" in text
    assert "浮盈" not in text
    assert "浮亏" not in text


def test_pnl_summary_account_equation(monkeypatch) -> None:
    monkeypatch.setattr(
        "quant.store.state.refresh_account_market_value",
        lambda holdings: {
            "可用资金": 50000.0,
            "持仓市值": 30000.0,
            "总资产": 80000.0,
            "当日已实现盈亏": 0.0,
        },
    )
    text = format_today_pnl_summary({}, date_str="2099-01-01", sync_account=True)
    assert "可用50000.00元 | 持仓市值30000.00元 | 总资产80000.00元" in text
    assert "当日盈亏：" in text
