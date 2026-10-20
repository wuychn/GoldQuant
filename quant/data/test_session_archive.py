"""盘后只拉涨停族。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from quant.data.session_archive import capture_session_extras


class CaptureSessionExtrasTests(unittest.TestCase):
    def test_captures_zt_and_boards(self):
        with (
            patch("quant.data.zt_pool.capture_zt_pool", return_value=1),
            patch("quant.data.board_pools.capture_board_pools", return_value={"zb": 1, "dt": 0, "prev": 2}),
        ):
            out = capture_session_extras("2026-10-09")
        self.assertEqual(out["zt_pool"], 1)
        self.assertEqual(out["zt_zb"], 1)
        self.assertEqual(out["zt_prev"], 2)
        self.assertEqual(out["zt_dt"], 0)
