"""capture_fund_flow_rank 业务层（mock 编排，无网络）。"""

from __future__ import annotations

import pandas as pd

from quant.store.paths import override_quant_home


def test_capture_fund_flow_rank_writes_snapshot(tmp_path, monkeypatch):
    from quant.data import factor_capture as fc
    from quant.data.fund_flow_5d import FundFlow5dResult
    from quant.data.factor_snapshots import read_fund_flow_snapshot

    with override_quant_home(tmp_path):
        as_of = "2026-08-08"
        spot = pd.DataFrame(
            {
                "code": ["000001", "600000", "300001"],
                "float_mv": [1e10, 2e10, 5e9],
            }
        )
        rank = pd.DataFrame(
            {
                "code": ["000001", "600000", "300001", "999999"],
                "main_net_inflow": [1e8, -2e8, 0.0, 9e9],
            }
        )

        def fake_fetch(**kwargs):
            return FundFlow5dResult(
                df=rank,
                sources_used=["eastmoney.clist_rank"],
                rank_pages_ok=1,
                rank_aborted=False,
            )

        monkeypatch.setattr(
            "quant.data.fund_flow_5d.fetch_main_net_inflow_5d",
            fake_fetch,
        )
        n = fc.capture_fund_flow_rank(
            as_of, spot, ["000001", "600000", "300001"], page_interval=10.0
        )
        snap = read_fund_flow_snapshot(as_of, exact=True)
        assert n == len(snap)
        assert set(snap) == {"000001", "600000", "300001"}
        assert abs(snap["000001"] - (1e8 / 1e10)) < 1e-9
        assert abs(snap["600000"] - (-2e8 / 2e10)) < 1e-9
        assert snap["300001"] == 0.0
