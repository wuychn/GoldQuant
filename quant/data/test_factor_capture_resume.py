"""fund_flow 分批落盘断点续传。"""

from __future__ import annotations

import pandas as pd
import pytest

from quant.store.paths import override_quant_home


@pytest.fixture()
def quant_tmp(tmp_path):
    with override_quant_home(tmp_path):
        yield tmp_path


def test_capture_fund_flow_batch_resume(quant_tmp, monkeypatch):
    from quant.data import factor_capture as fc

    as_of = "2026-08-07"
    codes = [f"{i:06d}" for i in range(1, 6)]
    spot = pd.DataFrame(
        {"code": codes, "float_mv": [1e10] * len(codes)}
    )
    calls: list[str] = []

    def fake_batch(batch: list[str]):
        calls.extend(batch)
        flows = {c: 1e8 for c in batch}
        return flows, set(batch), set()

    monkeypatch.setattr(fc, "_fetch_flows_5d_batch", fake_batch)

    n1 = fc.capture_fund_flow(as_of, spot, codes, flush_every=2)
    assert n1 == 5
    assert calls == codes
    assert (quant_tmp / "data" / "fund_flow_progress" / f"{as_of}.json").is_file()

    calls.clear()
    n2 = fc.capture_fund_flow(as_of, spot, codes, flush_every=2)
    assert n2 == 5
    assert calls == []  # 全部跳过


def test_capture_fund_flow_retries_errors(quant_tmp, monkeypatch):
    from quant.data import factor_capture as fc

    as_of = "2026-08-07"
    codes = ["000001", "000002"]
    spot = pd.DataFrame({"code": codes, "float_mv": [1e10, 1e10]})
    round_id = {"n": 0}

    def fake_batch(batch: list[str]):
        round_id["n"] += 1
        if round_id["n"] == 1:
            # 首轮：一只成功、一只失败
            return {"000001": 1e8}, {"000001"}, {"000002"}
        return {"000002": 2e8}, {"000002"}, set()

    monkeypatch.setattr(fc, "_fetch_flows_5d_batch", fake_batch)

    n1 = fc.capture_fund_flow(as_of, spot, codes, flush_every=10)
    assert n1 == 1
    n2 = fc.capture_fund_flow(as_of, spot, codes, flush_every=10)
    assert n2 == 2
    assert round_id["n"] == 2
