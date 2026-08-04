"""TencentDailySource：全部走腾讯（仅历史日 K）。

腾讯 ``stock_zh_a_hist_tx`` 提供历史 K（不复权/前/后复权、成交量统一为股、amount 转元、
含换手率），绕开东财 kline/clist 反爬。只实现 fetch_hist；其余接口显式 NotImplementedError。

接口级配置（quant.yml ``data.sources.daily`` dict）：
    daily:
      fetch_hist: tencent
"""

from __future__ import annotations

import pandas as pd

from quant.data.sources.daily._shared import _retry


def _tx_symbol(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("6", "9", "5")):
        return f"sh{c}"
    if c.startswith(("0", "2", "3", "1")):
        return f"sz{c}"
    if c.startswith(("4", "8")):
        return f"bj{c}"
    return f"sh{c}"


class TencentDailySource:
    name = "tencent"

    def fetch_hist(self, code: str, *, start: str, end: str, adjust: str = "") -> pd.DataFrame:
        import akshare as ak

        df = _retry(
            lambda: ak.stock_zh_a_hist_tx(
                symbol=_tx_symbol(code),
                start_date=start.replace("-", ""), end_date=end.replace("-", ""),
                adjust=adjust,
            ),
            label=f"hist_tx {code}", retries=3,
        )
        n = len(df)
        out = pd.DataFrame()
        out["code"] = [str(code).strip()] * n
        out["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        out["open"] = df["open"]
        out["high"] = df["high"]
        out["low"] = df["low"]
        out["close"] = df["close"]
        out["volume"] = df["volume"]
        out["amount"] = df["amount"]
        out["turnover_rate"] = df["turnover"]
        for c in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
            out[c] = pd.to_numeric(out[c], errors="coerce")
        return out

    # ---- 未实现接口：显式报错，不 fallback ----
    def fetch_spot(self) -> pd.DataFrame:
        raise NotImplementedError("tencent 未实现 fetch_spot（可用 sina/default/akshare）")

    def fetch_index(self, code: str = "000300", *, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError("tencent 未实现 fetch_index（可用 default/akshare）")

    def fetch_calendar(self) -> list[str]:
        raise NotImplementedError("tencent 未实现 fetch_calendar（可用 default/akshare）")

    def fetch_code_list(self) -> list[str]:
        raise NotImplementedError("tencent 未实现 fetch_code_list（可用 default/akshare）")

    def fetch_delisted_codes(self) -> pd.DataFrame:
        raise NotImplementedError("tencent 未实现 fetch_delisted_codes（可用 default/akshare）")

    def fetch_delisted_daily(self, code: str, *, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError("tencent 未实现 fetch_delisted_daily（可用 default/akshare）")
