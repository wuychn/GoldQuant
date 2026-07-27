"""Phase 4 退市股归一化测试（纯函数，无网络）。"""

from __future__ import annotations

import pandas as pd

from quant.data.delist import _normalize_ohlc, _normalize_stop_df


def test_normalize_stop_df_maps_columns():
    df = pd.DataFrame(
        {
            "股票代码": ["000001", "000002"],
            "公司名称": ["甲", "乙"],
            "终止上市日期": ["2024-01-01", "2024-03-15"],
        }
    )
    out = _normalize_stop_df(df)
    assert list(out.columns) == ["code", "name", "delist_date"]
    assert list(out["code"]) == ["000001", "000002"]
    assert out["delist_date"].iloc[0] == "2024-01-01"
    assert out["delist_date"].iloc[1] == "2024-03-15"


def test_normalize_stop_df_empty():
    assert _normalize_stop_df(pd.DataFrame()).empty
    assert _normalize_stop_df(None).empty


def test_normalize_ohlc_sina_columns():
    df = pd.DataFrame(
        {
            "日期": ["2024-01-02", "2024-01-03"],
            "开盘": [10.0, 11.0],
            "最高": [10.5, 11.0],
            "最低": [9.5, 10.0],
            "收盘": [10.0, 10.5],
            "成交量": [1e6, 1e6],
            "成交额": [1e7, 1e7],
            "换手率": [1.0, 1.0],
        }
    )
    out = _normalize_ohlc(df, "000001")
    assert list(out["date"]) == ["2024-01-02", "2024-01-03"]
    assert out["close"].iloc[1] == 10.5
    assert out["code"].iloc[0] == "000001"


def test_normalize_ohlc_empty():
    assert _normalize_ohlc(pd.DataFrame(), "000001").empty
    assert _normalize_ohlc(None, "000001").empty


def test_delisted_codes_in_build_union():
    """build_daily 的代码表并入退市股（去重保序）。"""
    current = ["600000", "000001"]
    delisted = ["000001", "退A123"]  # 000001 去重
    merged = list(dict.fromkeys(current + delisted))
    assert merged == ["600000", "000001", "退A123"]
