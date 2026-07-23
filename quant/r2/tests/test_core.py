"""R2 单元测试。"""

from __future__ import annotations

import unittest

from quant.r2.domain.models import Lifecycle, Regime
from quant.r2.market.regime import detect_regime
from quant.r2.sector.defensive import is_defensive_sector
from quant.r2.sector.engine import build_sector_rows, eligible_sector_names


class TestRegime(unittest.TestCase):
    def test_strong_market(self) -> None:
        payload = {
            "赚钱效应": {"上涨": 3000, "下跌": 1000, "涨停": 80},
            "大盘指数": [{"代码": "000001", "涨跌幅": 1.2}],
            "涨停统计": {"市场高度": "6连板", "今日涨停": [{}] * 80},
        }
        reg = detect_regime(payload)
        self.assertEqual(reg.regime, Regime.STRONG)
        self.assertGreater(reg.combat_cap, 0)

    def test_weak_market(self) -> None:
        payload = {
            "赚钱效应": {"上涨": 800, "下跌": 3500, "涨停": 20},
            "大盘指数": [{"代码": "000001", "涨跌幅": -1.5}],
            "涨停统计": {"市场高度": "2连板", "今日涨停": [{}] * 20},
        }
        reg = detect_regime(payload)
        self.assertEqual(reg.regime, Regime.WEAK)
        self.assertEqual(reg.combat_cap, 0)


class TestSector(unittest.TestCase):
    def test_defensive(self) -> None:
        self.assertTrue(is_defensive_sector("银行"))
        self.assertFalse(is_defensive_sector("CPO概念"))

    def test_build_sectors(self) -> None:
        payload = {
            "概念板块": {
                "涨幅榜": [
                    {"行业": "CPO概念", "行业-涨跌幅": 3.0, "净额": 10},
                ],
                "资金流入榜": [
                    {"行业": "CPO概念", "净额": 10},
                ],
            },
            "行业板块": {"涨幅榜": []},
            "赚钱效应": {"上涨": 2000, "下跌": 1500, "涨停": 50},
            "大盘指数": [{"代码": "000001", "涨跌幅": 0.3}],
        }
        rows = build_sector_rows(payload)
        names = eligible_sector_names(rows)
        self.assertIn("CPO概念", names)
        bank = [r for r in rows if r.name == "银行"]
        if bank:
            self.assertFalse(bank[0].eligible)


if __name__ == "__main__":
    unittest.main()
