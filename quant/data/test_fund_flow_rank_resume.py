"""fund_flow_rank 分页断点续传（mock 网络）。"""

from __future__ import annotations

import pandas as pd
import pytest

from quant.store.paths import override_quant_home


@pytest.fixture()
def quant_tmp(tmp_path):
    with override_quant_home(tmp_path):
        yield tmp_path


def _patch_fast(mod, monkeypatch):
    monkeypatch.setattr(mod, "_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(mod, "_MAX_INTERVAL", 0.0)
    monkeypatch.setattr(mod, "_BATCH_PAUSE_MIN", 0.0)
    monkeypatch.setattr(mod, "_BATCH_PAUSE_MAX", 0.0)
    monkeypatch.setattr(mod, "_BURST_PAGES_MIN", 100)
    monkeypatch.setattr(mod, "_BURST_PAGES_MAX", 100)
    monkeypatch.setattr(mod, "_rand_pause", lambda *a, **k: None)


def test_rank_resume_skips_fetched_pages(quant_tmp, monkeypatch):
    from quant.data.sources.eastmoney import fund_flow_rank as mod

    _patch_fast(mod, monkeypatch)
    calls: list[int] = []

    def fake_page(*, fid, fields, page, page_size):
        calls.append(page)
        rows = [
            {"f12": f"{page * 10 + i:06d}", "f164": float(page * 100 + i)}
            for i in range(2)
        ]
        return pd.DataFrame(rows), 6

    monkeypatch.setattr(mod, "_fetch_page_once", fake_page)

    as_of = "2026-08-08"
    df1 = mod.fetch_stock_fund_flow_rank(
        indicator="5日",
        page_size=2,
        page_interval=0.0,
        batch_pause_min_sec=0.0,
        batch_pause_max_sec=0.0,
        burst_pages_min=100,
        burst_pages_max=100,
        as_of=as_of,
        force=True,
    )
    assert len(df1) == 6
    assert calls == [1, 2, 3]

    calls.clear()
    df2 = mod.fetch_stock_fund_flow_rank(
        indicator="5日",
        page_size=2,
        page_interval=0.0,
        batch_pause_min_sec=0.0,
        batch_pause_max_sec=0.0,
        burst_pages_min=100,
        burst_pages_max=100,
        as_of=as_of,
        force=False,
    )
    assert len(df2) == 6
    assert calls == []


def test_rank_resume_continues_from_next_page(quant_tmp, monkeypatch):
    from quant.data.sources.eastmoney import fund_flow_rank as mod

    _patch_fast(mod, monkeypatch)
    calls: list[int] = []

    def fake_page(*, fid, fields, page, page_size):
        calls.append(page)
        rows = [
            {"f12": f"{page * 10 + i:06d}", "f164": float(page * 100 + i)}
            for i in range(2)
        ]
        return pd.DataFrame(rows), 6

    monkeypatch.setattr(mod, "_fetch_page_once", fake_page)

    as_of = "2026-08-08"
    page1 = pd.DataFrame(
        [
            {"code": "000010", "main_net_inflow": 100.0},
            {"code": "000011", "main_net_inflow": 101.0},
        ]
    )
    mod._save_page(as_of, "5日", 1, page1)
    mod._write_meta(
        as_of,
        "5日",
        {
            "indicator": "5日",
            "page_size": 2,
            "total": 6,
            "total_pages": 3,
            "next_page": 2,
            "last_page": 1,
            "done": False,
        },
    )

    df = mod.fetch_stock_fund_flow_rank(
        indicator="5日",
        page_size=2,
        page_interval=0.0,
        batch_pause_min_sec=0.0,
        batch_pause_max_sec=0.0,
        burst_pages_min=100,
        burst_pages_max=100,
        as_of=as_of,
        force=False,
    )
    assert calls == [2, 3]
    assert len(df) == 6


def test_rank_fail_skips_page_then_continues(quant_tmp, monkeypatch):
    from quant.data.sources.eastmoney import fund_flow_rank as mod

    _patch_fast(mod, monkeypatch)
    pauses: list[str] = []
    monkeypatch.setattr(
        mod,
        "_rand_pause",
        lambda lo, hi, *, what_next: pauses.append(what_next),
    )
    calls: list[int] = []

    def fake_page(*, fid, fields, page, page_size):
        calls.append(page)
        if page == 1:
            raise ConnectionError("RemoteDisconnected")
        rows = [
            {"f12": f"{page * 10 + i:06d}", "f164": float(page * 100 + i)}
            for i in range(2)
        ]
        # page2 成功时告知共 6 条 → 3 页；第 1 页已跳过
        return pd.DataFrame(rows), 6

    monkeypatch.setattr(mod, "_fetch_page_once", fake_page)

    df, meta = mod.fetch_stock_fund_flow_rank(
        indicator="5日",
        page_size=2,
        page_interval=0.0,
        batch_pause_min_sec=0.0,
        batch_pause_max_sec=0.0,
        burst_pages_min=100,
        burst_pages_max=100,
        as_of="2026-08-08",
        force=True,
        return_meta=True,
    )
    assert calls == [1, 2, 3]  # 第 1 页失败后跳到 2、3，不重打 1
    assert 1 in meta["skipped_pages"]
    assert len(df) == 4  # 仅 2、3 页
    assert any("跳过第 1 页" in p for p in pauses)
