"""盘中净额快照跟踪测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from quant.store import intraday_fund_track as mod


class IntradayFundTrackTests(unittest.TestCase):
    def test_improving_negative_flow(self) -> None:
        with patch.object(mod, "_load", return_value={"2026-06-16": {"000636": [-5000.0, -4200.0]}}):
            with patch.object(mod, "cn_date_str", return_value="2026-06-16"):
                self.assertTrue(mod.net_flow_improving("000636", min_delta_wan=50.0))

    def test_not_improving_when_worsening(self) -> None:
        with patch.object(mod, "_load", return_value={"2026-06-16": {"000636": [-3000.0, -5000.0]}}):
            with patch.object(mod, "cn_date_str", return_value="2026-06-16"):
                self.assertFalse(mod.net_flow_improving("000636", min_delta_wan=50.0))


if __name__ == "__main__":
    unittest.main()
