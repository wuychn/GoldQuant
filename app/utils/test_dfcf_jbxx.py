"""东财 jbxx 解析：兼容 API 顶层 ``dsc`` 等额外字段。"""

from __future__ import annotations

import unittest

from app.utils.dfcf_util import _parse_stock_individual_info_payload


class JbxxParseTests(unittest.TestCase):
    def test_parses_data_ignoring_dsc(self) -> None:
        payload = {
            "rc": 0,
            "rt": 4,
            "dlmkts": "",
            "dsc": "0",
            "data": {
                "f57": "600186",
                "f58": "莲花控股",
                "f127": "食品饮料",
                "f84": 1790000000.0,
                "f85": 1790000000.0,
                "f116": 10000000000.0,
                "f117": 10000000000.0,
                "f189": 19980831,
                "f43": 5.58,
            },
        }
        out = _parse_stock_individual_info_payload(payload)
        self.assertEqual(out["股票代码"], "600186")
        self.assertEqual(out["股票简称"], "莲花控股")
        self.assertEqual(out["行业"], "食品饮料")
        self.assertEqual(out["最新"], 5.58)

    def test_empty_when_no_data(self) -> None:
        self.assertEqual(_parse_stock_individual_info_payload({"rc": 0, "dsc": "0"}), {})
        self.assertEqual(_parse_stock_individual_info_payload({}), {})


if __name__ == "__main__":
    unittest.main()
