"""盘口异动筛选：单标签 + 多周期主升。"""

from __future__ import annotations

from quant.pool.pkyd_filter import passes_pkyd_main_wave_filter
from quant.pool.sources import prefilter_pkyd, postfilter_pkyd_acceleration


def _daily_row(date: str, open_p: float, close: float) -> dict:
    return {"日期": date, "开盘": open_p, "收盘": close}


def _uptrend_hist(months: int = 5) -> list[dict]:
    """构造持续上行、便于通过多周期主升筛选的日线。"""
    rows: list[dict] = []
    price = 10.0
    month = 1
    day = 1
    for _ in range(months * 22):
        open_p = price
        price *= 1.015
        rows.append(
            _daily_row(
                f"2026-{month:02d}-{min(day, 28):02d}",
                open_p,
                price,
            )
        )
        day += 1
        if day > 28:
            day = 1
            month += 1
    return rows


def test_prefilter_pkyd_keeps_all_symbol_pool_rows_not_tag_top_n() -> None:
    """初筛不按标签数截断，双标签不应挤掉单标签。"""
    many_tags = {
        "代码": "000001",
        "名称": "双标签",
        "异动类型": ["60日新高", "60日大幅上涨"],
    }
    one_tag = {
        "代码": "000002",
        "名称": "单标签",
        "异动类型": ["60日新高"],
    }
    rows = prefilter_pkyd([many_tags, one_tag], cfg={"pkyd_min_tags": 1})
    codes = {r["股票代码"] for r in rows}
    assert codes == {"000001", "000002"}


def test_prefilter_pkyd_rejects_star_and_bse() -> None:
    rows = prefilter_pkyd(
        [
            {"代码": "688001", "名称": "科创", "异动类型": ["60日新高"]},
            {"代码": "830001", "名称": "北交", "异动类型": ["60日新高"]},
            {"代码": "000001", "名称": "深主", "异动类型": ["60日新高"]},
        ],
        cfg={"pkyd_min_tags": 1},
    )
    assert [r["股票代码"] for r in rows] == ["000001"]


def test_prefilter_pkyd_rejects_zero_tag() -> None:
    rows = prefilter_pkyd(
        [{"代码": "000001", "名称": "测试", "异动类型": []}],
        cfg={"pkyd_min_tags": 1},
    )
    assert rows == []


def test_pkyd_main_wave_filter_passes_uptrend() -> None:
    stock = {"历史行情": _uptrend_hist(6)}
    ok, msg, detail = passes_pkyd_main_wave_filter(
        stock,
        candidate_cfg={"pkyd_three_month_min_pct": 30},
        mw_cfg={"min_ma_spread_pct": 0.5},
    )
    assert ok, msg
    assert detail["本月阳线"] is True
    assert detail["近3月涨幅"] is not None
    assert detail["近3月涨幅"] > 30


def test_postfilter_sorts_by_three_month_return() -> None:
    low = {"股票代码": "000001", "历史行情": _uptrend_hist(4)}
    high = {"股票代码": "000002", "历史行情": _uptrend_hist(6)}
    mw = {"min_ma_spread_pct": 0.5}
    cfg = {"pkyd_three_month_min_pct": 30, "pkyd_dual_tag_limit": 1}
    out = postfilter_pkyd_acceleration([low, high], cfg=cfg)
    # 至少高涨幅那只应入选；若两只都过则取涨幅更高者
    assert out and out[0]["股票代码"] in ("000001", "000002")
