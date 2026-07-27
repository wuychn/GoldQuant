"""maintain ``scan_missing_dates`` 单测。"""

from __future__ import annotations

import pandas as pd

from scripts.data import maintain as m


def test_scan_missing_dates_finds_gap(monkeypatch):
    """有 d1/d3 缺 d2/d4 → 返回缺失。"""
    monkeypatch.setattr(
        m,
        "read_daily_raw",
        lambda **kw: pd.DataFrame({"date": ["2026-07-21", "2026-07-23"]}),
    )
    monkeypatch.setattr(
        m,
        "read_calendar",
        lambda: ["2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"],
    )
    miss = m.scan_missing_dates("2026-07-24")
    assert miss == ["2026-07-22", "2026-07-24"]


def test_scan_missing_dates_empty_store(monkeypatch):
    """空库 → 全部交易日都缺。"""
    monkeypatch.setattr(m, "read_daily_raw", lambda **kw: pd.DataFrame())
    monkeypatch.setattr(m, "read_calendar", lambda: ["2026-07-21", "2026-07-22"])
    miss = m.scan_missing_dates("2026-07-22")
    assert miss == ["2026-07-21", "2026-07-22"]


def test_scan_missing_dates_complete(monkeypatch):
    """无缺口 → 返回空。"""
    monkeypatch.setattr(
        m,
        "read_daily_raw",
        lambda **kw: pd.DataFrame({"date": ["2026-07-21", "2026-07-22"]}),
    )
    monkeypatch.setattr(m, "read_calendar", lambda: ["2026-07-21", "2026-07-22"])
    miss = m.scan_missing_dates("2026-07-22")
    assert miss == []


def test_scan_missing_dates_respects_as_of(monkeypatch):
    """as_of 之后的交易日不计入缺口。"""
    monkeypatch.setattr(
        m,
        "read_daily_raw",
        lambda **kw: pd.DataFrame({"date": ["2026-07-21"]}),
    )
    monkeypatch.setattr(
        m,
        "read_calendar",
        lambda: ["2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24"],
    )
    miss = m.scan_missing_dates("2026-07-22")  # 截至日，07-23/24 不算
    assert miss == ["2026-07-22"]
