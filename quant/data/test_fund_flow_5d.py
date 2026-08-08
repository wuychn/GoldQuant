"""fund_flow_5d 编排：分页后逐票补缺，已有码不重复拉。"""

from __future__ import annotations

import pandas as pd
import pytest

from quant.store.paths import override_quant_home


@pytest.fixture()
def quant_tmp(tmp_path):
    with override_quant_home(tmp_path):
        yield tmp_path


def test_after_rank_fills_missing_only(quant_tmp, monkeypatch):
    from quant.data import fund_flow_5d as orch

    as_of = "2026-08-08"
    rank_df = pd.DataFrame(
        {"code": ["000001", "000002"], "main_net_inflow": [1e8, 2e8]}
    )

    monkeypatch.setattr(
        "quant.data.sources.interface.try_with_fallback",
        lambda *a, **kw: (
            rank_df,
            {"aborted": False, "abort_reason": "", "last_page": 1, "done": True},
        ),
    )
    monkeypatch.setattr(orch, "_page_interval_bounds", lambda: (0.0, 0.0))
    monkeypatch.setattr(orch, "_symbol_interval_bounds", lambda: (0.0, 0.0))
    monkeypatch.setattr(orch, "_batch_pause_bounds", lambda: (0.0, 0.0))
    monkeypatch.setattr(orch, "_burst_pages", lambda: (2, 4))

    pulled: list[str] = []

    def fake_fill(**kwargs):
        pulled.extend(list(kwargs["codes"]))
        codes = kwargs["codes"]
        filled = {c: float(i + 1) * 1e8 for i, c in enumerate(codes)}
        return len(filled), 0, filled

    monkeypatch.setattr(orch, "_fill_per_symbol", fake_fill)

    result = orch.fetch_main_net_inflow_5d(
        as_of=as_of,
        codes=["000001", "000002", "000003"],
        page_size=100,
        force=False,
    )
    assert "enrich.fetch_stock_fund_flow_daily" in result.sources_used
    assert pulled == ["000003"]
    assert set(result.df["code"]) == {"000001", "000002", "000003"}


def test_use_rank_false_skips_pagination(quant_tmp, monkeypatch):
    from quant.data import fund_flow_5d as orch

    monkeypatch.setattr(orch, "_page_interval_bounds", lambda: (0.0, 0.0))
    monkeypatch.setattr(orch, "_symbol_interval_bounds", lambda: (0.0, 0.0))
    monkeypatch.setattr(orch, "_batch_pause_bounds", lambda: (0.0, 0.0))
    monkeypatch.setattr(orch, "_burst_pages", lambda: (1, 3))

    def boom_rank(*a, **kw):
        raise AssertionError("should not call rank when use_rank=false")

    monkeypatch.setattr(
        "quant.data.sources.interface.try_with_fallback", boom_rank
    )
    pulled: list[str] = []

    def fake_fill(**kwargs):
        codes = kwargs["codes"]
        pulled.extend(list(codes))
        return len(codes), 0, {c: 1.0 for c in codes}

    monkeypatch.setattr(orch, "_fill_per_symbol", fake_fill)

    result = orch.fetch_main_net_inflow_5d(
        as_of="2026-08-08",
        codes=["000001", "000002"],
        use_rank=False,
    )
    assert pulled == ["000001", "000002"]
    assert not any("fund_flow_rank" in s for s in result.sources_used)
    assert "enrich.fetch_stock_fund_flow_daily" in result.sources_used
    assert len(result.df) == 2


def test_no_per_symbol_when_rank_covers(quant_tmp, monkeypatch):
    from quant.data import fund_flow_5d as orch

    rank_df = pd.DataFrame(
        {
            "code": ["000001", "000002", "000003"],
            "main_net_inflow": [1.0, 2.0, 3.0],
        }
    )
    monkeypatch.setattr(
        "quant.data.sources.interface.try_with_fallback",
        lambda *a, **kw: (
            rank_df,
            {"aborted": False, "last_page": 1, "abort_reason": "", "done": True},
        ),
    )
    monkeypatch.setattr(orch, "_page_interval_bounds", lambda: (0.0, 0.0))
    monkeypatch.setattr(orch, "_symbol_interval_bounds", lambda: (0.0, 0.0))
    monkeypatch.setattr(orch, "_batch_pause_bounds", lambda: (0.0, 0.0))
    monkeypatch.setattr(orch, "_burst_pages", lambda: (2, 4))

    called = {"n": 0}

    def boom(**kwargs):
        called["n"] += 1
        raise AssertionError("should not per_symbol")

    monkeypatch.setattr(orch, "_fill_per_symbol", boom)

    result = orch.fetch_main_net_inflow_5d(
        as_of="2026-08-08",
        codes=["000001", "000002", "000003"],
    )
    assert called["n"] == 0
    assert any("fetch_stock_fund_flow_rank" in s for s in result.sources_used)
    assert len(result.df) == 3
