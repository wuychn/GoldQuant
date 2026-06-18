"""``Response`` 测试阶段截断。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.schemas.response import Response


class ResponseTestPhaseTrimTests(unittest.TestCase):
    @patch("app.core.config.get_settings")
    def test_response_trims_when_test_phase(self, mock_get_settings) -> None:
        mock_get_settings.return_value.quant_test_list_limit = lambda: 3
        resp = Response(data={"codes": list(range(20))})
        self.assertEqual(resp.data["codes"], [0, 1, 2])

    @patch("app.core.config.get_settings")
    def test_response_passthrough_when_not_test_phase(self, mock_get_settings) -> None:
        mock_get_settings.return_value.quant_test_list_limit = lambda: None
        data = {"codes": list(range(20))}
        resp = Response(data=data)
        self.assertEqual(resp.data["codes"], list(range(20)))


if __name__ == "__main__":
    unittest.main()
