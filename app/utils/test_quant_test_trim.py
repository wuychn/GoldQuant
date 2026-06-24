"""``quant_test_trim`` 单元测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.utils.quant_test_trim import maybe_trim_for_test_phase, trim_for_test_phase


class QuantTestTrimTests(unittest.TestCase):
    def test_trim_nested_lists(self) -> None:
        payload = {"items": list(range(10)), "nested": {"codes": ["a"] * 8}}
        out = trim_for_test_phase(payload, limit=3)
        self.assertEqual(out["items"], [0, 1, 2])
        self.assertEqual(out["nested"]["codes"], ["a", "a", "a"])

    def test_trim_rows_updates_row_count(self) -> None:
        payload = {
            "source": "akshare.foo",
            "row_count": 100,
            "rows": [{"x": i} for i in range(20)],
        }
        out = trim_for_test_phase(payload, limit=3)
        self.assertEqual(len(out["rows"]), 3)
        self.assertEqual(out["row_count"], 3)

    @patch("app.core.config.get_settings")
    def test_maybe_trim_disabled(self, mock_get_settings) -> None:
        mock_get_settings.return_value.quant_test_list_limit = lambda: None
        data = {"rows": [1, 2, 3, 4, 5]}
        self.assertEqual(maybe_trim_for_test_phase(data), data)

    @patch("app.core.config.get_settings")
    def test_maybe_trim_enabled(self, mock_get_settings) -> None:
        mock_get_settings.return_value.quant_test_list_limit = lambda: 3
        data = {"rows": list(range(10))}
        out = maybe_trim_for_test_phase(data)
        self.assertEqual(out["rows"], [0, 1, 2])

    @patch("app.core.config.get_settings")
    def test_truncate_list_for_test_phase(self, mock_get_settings) -> None:
        from app.utils.quant_test_trim import truncate_list_for_test_phase

        mock_get_settings.return_value.quant_test_list_limit = lambda: 3
        self.assertEqual(truncate_list_for_test_phase(list(range(8))), [0, 1, 2])
        mock_get_settings.return_value.quant_test_list_limit = lambda: None
        self.assertEqual(truncate_list_for_test_phase(list(range(8))), list(range(8)))

    @patch("app.core.config.get_settings")
    def test_test_phase_list_limit(self, mock_get_settings) -> None:
        from app.utils.quant_test_trim import test_phase_list_limit

        mock_get_settings.return_value.quant_test_list_limit = lambda: 3
        self.assertEqual(test_phase_list_limit(default=10), 3)
        mock_get_settings.return_value.quant_test_list_limit = lambda: None
        self.assertEqual(test_phase_list_limit(default=10), 10)

    @patch("app.core.config.get_settings")
    def test_trim_quant_payload(self, mock_get_settings) -> None:
        from app.utils.quant_test_trim import trim_quant_payload

        mock_get_settings.return_value.quant_test_list_limit = lambda: 3
        payload = {"自选股": list(range(8)), "持仓股": list(range(5))}
        out = trim_quant_payload(payload)
        self.assertEqual(out["自选股"], [0, 1, 2])
        self.assertEqual(out["持仓股"], [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
