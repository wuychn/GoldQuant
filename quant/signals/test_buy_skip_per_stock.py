"""买入信号：数据质量按票跳过，不连坐全池。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from quant.constants import STRATEGY_NAME
from quant.scoring.context import ScoreContext
from quant.scoring.models import StockScore
from quant.signals.buy import generate_buy_signals


def _stock(code: str, name: str, chg: float = 3.0) -> dict:
    return {
        "股票代码": code,
        "股票名称": name,
        "盘口": {"最新": 10.0, "涨幅": chg, "均价": 9.8, "最高": 10.1},
        "分钟行情": [
            {
                "时间": f"2026-06-16 09:{30 + i}:00",
                "开盘": 9.8 + i * 0.02,
                "收盘": 9.82 + i * 0.02,
                "成交量": 1000 + i * 50,
            }
            for i in range(8)
        ],
        "历史行情": [{"日期": "2026-06-10", "收盘": 9.0}] * 30,
    }


class BuySkipPerStockTests(unittest.TestCase):
    @patch("quant.signals.buy.get_holdings", return_value=[])
    @patch("quant.signals.buy.active_holding_count", return_value=0)
    @patch("quant.signals.buy.position_limits", return_value={"max_stocks": 3})
    @patch("quant.signals.buy.allocate_buy_quantities_by_score", return_value={"000636": 100})
    @patch("quant.signals.buy._evaluate_buy_candidate")
    def test_skip_codes_do_not_block_other_stocks(
        self, mock_eval, *_mocks
    ) -> None:
        from quant.signals import buy as mod

        good = _stock("000636", "风华高科")
        bad = _stock("603065", "宿迁联盛")
        payload = {
            "大盘指数": [{"涨跌幅": 0.5}],
            "赚钱效应": {"上涨": 2000, "下跌": 1500},
            "自选股": [bad, good],
            "_data_quality": {
                "block_intraday_buy": False,
                "skip_intraday_buy_codes": ["603065"],
            },
        }
        ctx = ScoreContext(payload=payload, mode="during_market")

        def side_effect(stock, *args, **kwargs):
            code = stock.get("股票代码")
            if code == "603065":
                return None
            return mod._BuyCandidate(
                score=StockScore(
                    code="000636",
                    name="风华高科",
                    total=80.0,
                    strategy=STRATEGY_NAME,
                    passed_threshold=True,
                ),
                code="000636",
                name="风华高科",
                price=10.0,
                kind="上升途中",
                reason="主升波段上升途中",
                intra_note="分时强势",
                stock=good,
            )

        mock_eval.side_effect = side_effect
        sigs = generate_buy_signals(ctx, mode="during_market")
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0].code, "000636")
        called_codes = [c.args[0].get("股票代码") for c in mock_eval.call_args_list]
        self.assertEqual(called_codes, ["603065", "000636"])


if __name__ == "__main__":
    unittest.main()
