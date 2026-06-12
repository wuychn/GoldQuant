"""模拟撮合费用规则测试。"""

from __future__ import annotations

import unittest

from quant.execution.sim_rules import TradeSimConfig, calc_buy_cost, calc_commission, calc_sell_proceeds


class SimRulesTests(unittest.TestCase):
    def test_commission_wan_yi_min_five(self) -> None:
        cfg = TradeSimConfig(commission_rate=0.0001, min_commission=5.0)
        self.assertEqual(calc_commission(1000, cfg), 5.0)
        self.assertEqual(calc_commission(100_000, cfg), 10.0)

    def test_sell_includes_stamp_tax(self) -> None:
        cfg = TradeSimConfig(commission_rate=0.0001, min_commission=5.0, stamp_tax_rate=0.0005)
        r = calc_sell_proceeds(10.0, 1000, "600000", 9.0, cfg)
        self.assertGreater(r.stamp_tax, 0)
        self.assertLess(r.fill_price, 10.0)

    def test_shanghai_transfer_fee(self) -> None:
        cfg = TradeSimConfig(transfer_fee_rate=0.00001)
        buy = calc_buy_cost(10.0, 1000, "600000", cfg)
        sz = calc_buy_cost(10.0, 1000, "000001", cfg)
        self.assertGreater(buy.transfer_fee, 0)
        self.assertEqual(sz.transfer_fee, 0)


if __name__ == "__main__":
    unittest.main()
