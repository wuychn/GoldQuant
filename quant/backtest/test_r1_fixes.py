"""r1 修补回归测试：_inject_state 字段合并 + 停牌启发式 + 日历。

锁定评估文档发现的「回测零成交」修复：_inject_state 必须保留 rich 行的行情字段，
仅叠加 thin 行的元数据。同时验证停牌启发式不再被技术指标绕过、日历死代码已清。
"""

from __future__ import annotations

from datetime import date

from quant.backtest.engine import (
    WATCHLIST_META_KEYS,
    enrich_holdings_with_payload,
    index_payload_stocks,
)
from quant.data.suspension import is_suspended_from_row
from quant.research.state import MemoryPortfolioState
from quant.backtest.simulator import _inject_state


def _rich_row(code: str, name: str = "甲股") -> dict:
    return {
        "股票代码": code,
        "股票名称": name,
        "盘口": {"最新": 10.5, "今开": 10.3, "最高": 10.8, "最低": 10.1},
        "历史行情": [{"日期": "2024-01-01", "收盘": 10.0}, {"日期": "2024-01-02", "收盘": 10.5}],
        "技术指标": {"last_close": 10.5},
        "上市时间": "2010-01-01",
        "所属概念": ["概念A"],
    }


def _thin_watchlist(code: str, score: float = 80.0) -> dict:
    return {
        "股票代码": code,
        "股票名称": "甲股",
        "评分": score,
        "加入自选原因": "动量加速",
        "战法": "主升",
    }


def test_index_payload_stocks_prefers_rich():
    payload = {"自选股": [_rich_row("000001")], "_observe_enriched": [_rich_row("000002")]}
    idx = index_payload_stocks(payload)
    assert "000001" in idx and "000002" in idx
    assert idx["000001"]["盘口"]["最新"] == 10.5


def test_inject_state_preserves_market_data():
    """核心回归：合并后 rich 行的 历史行情/盘口 必须保留，元数据 评分 被叠加。"""
    payload = {"自选股": [_rich_row("000001")]}
    mem = MemoryPortfolioState(cash=1_000_000)
    mem.watchlist = [_thin_watchlist("000001", score=85.0)]

    out = _inject_state(payload, mem)
    wl = out["自选股"]
    assert len(wl) == 1
    row = wl[0]
    # 行情字段保留
    assert row["盘口"]["最新"] == 10.5
    assert len(row["历史行情"]) == 2
    # 元数据叠加
    assert row["评分"] == 85.0
    assert row["加入自选原因"] == "动量加速"
    # 代码/名称正确
    assert row["股票代码"] == "000001"


def test_inject_state_thin_only_falls_back():
    """代码不在 payload 时回退到 thin 行（不崩）。"""
    payload = {"自选股": []}
    mem = MemoryPortfolioState(cash=1_000_000)
    mem.watchlist = [_thin_watchlist("000999")]
    out = _inject_state(payload, mem)
    assert len(out["自选股"]) == 1
    assert out["自选股"][0]["股票代码"] == "000999"


def test_enrich_holdings_with_payload():
    payload = {"自选股": [_rich_row("000001")]}
    holdings = [{"股票代码": "000001", "买入价": 10.0, "持仓股数": 100, "买入日期": "2024-01-01"}]
    out = enrich_holdings_with_payload(holdings, payload)
    assert len(out) == 1
    h = out[0]
    # 持仓元数据保留
    assert h["买入价"] == 10.0 and h["持仓股数"] == 100
    # 行情字段从 rich 行补齐
    assert h["盘口"]["最新"] == 10.5
    assert len(h["历史行情"]) == 2


def test_suspension_not_bypassed_by_technical_indicators():
    """盘口为空但有归档技术指标时，仍应判停牌（修复前会被绕过）。"""
    suspended = {"股票代码": "000001", "盘口": {}, "技术指标": {"last_close": 10.0}}
    assert is_suspended_from_row(suspended) is True
    # 无盘口字段
    assert is_suspended_from_row({"股票代码": "000002"}) is True
    # 正常在市
    active = {"股票代码": "000003", "盘口": {"最新": 10.0, "今开": 9.9}, "技术指标": {}}
    assert is_suspended_from_row(active) is False


def test_calendar_no_dead_code_or_true():
    """calendar.py 的 `or True` 死代码已移除（抓取失败不再写空数组污染缓存）。"""
    import inspect

    from quant.data import calendar

    src = inspect.getsource(calendar._fetch_and_cache)
    assert "or True" not in src
    # is_trading_day 用 mtime 失效加载
    assert calendar.is_trading_day(date(2024, 1, 2)) in (True, False)  # 不崩即可
