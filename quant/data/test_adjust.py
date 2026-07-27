"""Phase 1 复权一致性回归（KEYSTONE）。

验证：IC 前瞻收益、因子、出场 ATR 所依赖的价格序列统一在后复权基准上，
除权日不会产生假跳跌（修复前 forward_return/IC 标签被原始价污染的 bug）。
"""

from __future__ import annotations

import pandas as pd

from quant.data.adjust import apply_hfq, load_adjusted_daily
from quant.factors.panel_builder import _forward_returns_panel


def test_apply_hfq_smooths_dividend_drop():
    """除权日 raw close 跳跌（10→5，10 送 10），hfq 因子应使后复权序列连续。"""
    dates = [f"2024-01-{i:02d}" for i in range(1, 7)]
    raw = pd.DataFrame(
        {
            "code": ["000001"] * 6,
            "date": dates,
            "open": [10.0] * 3 + [5.0] * 3,
            "close": [10.0] * 3 + [5.0] * 3,
        }
    )
    adj = pd.DataFrame(
        {
            "code": ["000001"] * 6,
            "date": dates,
            "hfq_factor": [1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
        }
    )
    out = apply_hfq(raw, adj)
    # 后复权 OHLC 全程为 10（连续，除权日无跳跌）
    assert list(out["close"]) == [10.0] * 6
    assert list(out["open"]) == [10.0] * 6


def test_forward_returns_on_adjusted_no_fake_drop():
    """前瞻收益在复权帧上跨除权日应≈0，而非原始价的 -50% 假跳跌。"""
    dates = [f"2024-01-{i:02d}" for i in range(1, 11)]
    raw_close = [10.0] * 3 + [5.0] * 7
    factor = [1.0] * 3 + [2.0] * 7
    adj_close = [r * f for r, f in zip(raw_close, factor)]  # 后复权恒 10
    daily = pd.DataFrame({"code": ["000001"] * 10, "date": dates, "close": adj_close})
    res = _forward_returns_panel(daily, {"000001"}, {dates[0]}, horizons=(5,))
    fwd5 = res[(dates[0], "000001")][5]
    assert abs(fwd5 - 0.0) < 1e-6, f"复权帧 5 日前瞻收益应≈0，实际 {fwd5}（疑似原始价污染）"


def test_load_adjusted_daily_merges_adj(monkeypatch):
    """load_adjusted_daily 必须把 raw 与 adj_factor 合并（KEYSTONE 加载边界）。"""
    import quant.data.adjust as adj_mod

    dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
    raw = pd.DataFrame(
        {"code": ["000001"] * 3, "date": dates, "close": [10.0, 5.0, 5.0]}
    )
    af = pd.DataFrame(
        {"code": ["000001"] * 3, "date": dates, "hfq_factor": [1.0, 2.0, 2.0]}
    )
    monkeypatch.setattr(adj_mod, "read_daily_raw", lambda **kw: raw)
    monkeypatch.setattr(adj_mod, "read_adj_factor", lambda **kw: af)
    out = load_adjusted_daily()
    assert list(out["close"]) == [10.0, 10.0, 10.0]


def test_load_adjusted_daily_empty_when_no_raw(monkeypatch):
    """raw 为空时直接返回空帧，不报错。"""
    import quant.data.adjust as adj_mod

    monkeypatch.setattr(adj_mod, "read_daily_raw", lambda **kw: pd.DataFrame(columns=["code", "date"]))
    out = load_adjusted_daily()
    assert out.empty
