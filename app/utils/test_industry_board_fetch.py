"""industry_board_fetch 解析。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.utils.industry_board_fetch import fetch_em_industry_board, fetch_ths_industry_names


class EmIndustryFetchTests(unittest.TestCase):
    @patch("app.utils.industry_board_fetch.requests.get")
    def test_fetch_em_pagination(self, mock_get: MagicMock) -> None:
        page1 = MagicMock()
        page1.json.return_value = {
            "data": {
                "total": 2,
                "diff": [
                    {"f12": "BK0001", "f14": "元件"},
                    {"f12": "BK0002", "f14": "半导体"},
                ],
            }
        }
        mock_get.return_value = page1
        rows = fetch_em_industry_board(timeout=5, retries=1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["板块名称"], "元件")


class ThsIndustryFetchTests(unittest.TestCase):
    @patch("app.utils.industry_board_fetch.requests.get")
    @patch("app.utils.industry_board_fetch._ths_cookie_v", return_value="abc")
    def test_fetch_ths_names(self, _v: MagicMock, mock_get: MagicMock) -> None:
        html = """
        <html><body><div class="cate_inner">
          <a href="/thshy/detail/code/881121/">元件</a>
          <a href="/thshy/detail/code/881122/">半导体</a>
        </div></body></html>
        """
        resp = MagicMock()
        resp.text = html
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        rows = fetch_ths_industry_names(timeout=5, retries=1)
        self.assertEqual([r["name"] for r in rows], ["元件", "半导体"])
        self.assertEqual(rows[0]["code"], "881121")


if __name__ == "__main__":
    unittest.main()
