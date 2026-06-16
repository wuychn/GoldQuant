"""同花顺形态榜：合并、初筛、拆分。"""

from __future__ import annotations

from quant.pool.candidate_config import (
    PAYLOAD_KEY_CXFL,
    PAYLOAD_KEY_CXG,
    PAYLOAD_KEY_LJQS,
    PAYLOAD_KEY_LXSZ,
)
from quant.pool.sources import merge_ths_rank_from_batches, prefilter_ths_rank
from quant.pool.ths_rank_util import (
    format_ths_rank_watchlist_reason,
    merge_ths_rank_rows_by_code,
    split_enriched_ths_rank_payload,
)


def test_merge_ths_rank_rows_dedupes_by_code() -> None:
    merged = merge_ths_rank_rows_by_code(
        entries=[
            ("000001", "平安银行", "创月新高"),
            ("000001", "平安银行", "半年新高"),
            ("000002", "万科A", "量价齐升"),
        ],
    )
    by_code = {r["股票代码"]: r for r in merged}
    assert set(by_code) == {"000001", "000002"}
    assert set(by_code["000001"]["榜单标签"]) == {"创月新高", "半年新高"}


def test_prefilter_ths_rank_rejects_star_and_bse() -> None:
    rows = prefilter_ths_rank(
        [
            {"股票代码": "688001", "股票名称": "华兴源创", "榜单标签": ["创月新高"]},
            {"股票代码": "000001", "股票名称": "*ST平安", "榜单标签": ["创月新高"]},
            {"股票代码": "300001", "股票名称": "特锐德", "榜单标签": ["持续放量"]},
        ],
    )
    codes = {r["股票代码"] for r in rows}
    assert codes == {"300001"}


def test_merge_ths_rank_from_batches() -> None:
    batches = [
        ("创月新高", [{"股票代码": "000001", "股票简称": "平安银行"}]),
        ("量价齐升", [{"股票代码": "000001", "股票简称": "平安银行"}]),
    ]
    rows = merge_ths_rank_from_batches(batches)
    assert len(rows) == 1
    assert set(rows[0]["榜单标签"]) == {"创月新高", "量价齐升"}


def test_split_enriched_ths_rank_payload() -> None:
    row = {
        "股票代码": "000001",
        "榜单标签": ["创月新高", "量价齐升"],
    }
    out = split_enriched_ths_rank_payload([row])
    assert len(out[PAYLOAD_KEY_CXG]) == 1
    assert len(out[PAYLOAD_KEY_LJQS]) == 1
    assert out[PAYLOAD_KEY_LXSZ] == []
    assert out[PAYLOAD_KEY_CXFL] == []


def test_format_ths_rank_watchlist_reason() -> None:
    text = format_ths_rank_watchlist_reason(["创月新高", "半年新高", "量价齐升"])
    assert "创新高(创月新高、半年新高)" in text
    assert "量价齐升" in text
