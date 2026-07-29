"""东财 jbxx 解析测试。"""

from quant.data.sources.eastmoney.dfcf import _parse_stock_individual_info_payload


def test_parse_jbxx_payload():
    raw = {
        "data": {
            "f57": "600519",
            "f58": "贵州茅台",
            "f127": "白酒",
        }
    }
    out = _parse_stock_individual_info_payload(raw)
    assert out["股票代码"] == "600519"
    assert out["股票简称"] == "贵州茅台"
