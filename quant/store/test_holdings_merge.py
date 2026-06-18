"""持仓与 payload 合并测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from quant.narrative.during_market_push import build_during_market_push
from quant.store.state import merge_holding_meta, merge_payload_holdings, resolve_payload_holdings


class HoldingMergeTests(unittest.TestCase):
    def test_merge_holding_meta_overlays_cost_and_qty(self) -> None:
        enriched = {
            "股票代码": "601016",
            "股票名称": "节能风电",
            "盘口": {"最新": 4.68, "涨幅": -2.09},
        }
        state = {
            "股票代码": "601016",
            "股票名称": "节能风电",
            "买入价": 5.37,
            "持仓股数": 500,
        }
        merged = merge_holding_meta(enriched, state)
        self.assertEqual(merged["买入价"], 5.37)
        self.assertEqual(merged["持仓股数"], 500)
        self.assertEqual(merged["盘口"]["最新"], 4.68)

    @patch("quant.store.state.get_holdings")
    def test_resolve_payload_holdings_merges_state(self, mock_holdings) -> None:
        mock_holdings.return_value = [
            {
                "股票代码": "601016",
                "股票名称": "节能风电",
                "买入价": 5.37,
                "持仓股数": 500,
            }
        ]
        payload = {
            "持仓股": [
                {
                    "股票代码": "601016",
                    "股票名称": "节能风电",
                    "盘口": {"最新": 4.68, "涨幅": -2.09},
                }
            ]
        }
        rows = resolve_payload_holdings(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["买入价"], 5.37)
        self.assertEqual(rows[0]["持仓股数"], 500)

    @patch("quant.store.state.get_holdings")
    def test_push_shows_holding_pnl(self, mock_holdings) -> None:
        mock_holdings.return_value = [
            {
                "股票代码": "601016",
                "股票名称": "节能风电",
                "买入价": 5.37,
                "持仓股数": 500,
            }
        ]
        text = build_during_market_push(
            {
                "持仓股": [
                    {
                        "股票代码": "601016",
                        "股票名称": "节能风电",
                        "盘口": {"最新": 4.68, "涨幅": -2.09},
                    }
                ],
                "自选股": [],
            },
            timestamp="2026-06-18 14:35:00",
        )
        self.assertIn("节能风电", text)
        self.assertIn("成本5.37", text)
        self.assertIn("现价4.68", text)
        self.assertIn("浮亏-", text)


if __name__ == "__main__":
    unittest.main()
