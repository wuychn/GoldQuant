"""Akshare global news source (rate-limited)."""

from __future__ import annotations

from typing import Any

from app.utils.common_util import get_val
from app.utils.dataframe import dataframe_to_records
from quant.data.sources.rate_limit import with_limit


class AkshareNewsSource:
    def fetch_em(self) -> list[dict[str, Any]]:
        import akshare as ak

        def _call():
            return ak.stock_info_global_em()

        try:
            data = dataframe_to_records(with_limit("akshare", _call)) or []
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for d in data:
            if get_val(d, "标题") or get_val(d, "摘要"):
                row = {"标题": get_val(d, "标题"), "摘要": get_val(d, "摘要"), "来源": "东方财富"}
                p = get_val(d, "发布时间")
                if p not in (None, ""):
                    row["发布时间"] = str(p).strip()
                out.append(row)
        return out

    def fetch_ths(self) -> list[dict[str, Any]]:
        import akshare as ak

        def _call():
            return ak.stock_info_global_ths()

        try:
            data = dataframe_to_records(with_limit("akshare", _call)) or []
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for d in data:
            if get_val(d, "标题") or get_val(d, "内容"):
                pt = get_val(d, "发布时间")
                out.append(
                    {
                        "标题": get_val(d, "标题"),
                        "摘要": get_val(d, "内容"),
                        "发布时间": str(pt).strip() if pt not in (None, "") else "",
                        "来源": "同花顺",
                    }
                )
        return out

    def fetch_global(self) -> list[dict[str, Any]]:
        return self.fetch_em() + self.fetch_ths()
