"""SinaDailySource：全部走新浪（spot + hist）。

只实现新浪支持的接口；未实现的接口显式抛 ``NotImplementedError``，不隐式 fallback
到别的源（接口级切换原则：facade 一个方法 = 一个源，业务层自行组合）。

接口级配置（quant.yml ``data.sources.daily`` 为 dict 时按接口选源）：
    daily:
      fetch_spot: sina
      fetch_hist: sina
"""

from __future__ import annotations

import json
import time

import pandas as pd

from quant.data.sources.daily._shared import _retry

_SPOT_COLUMNS = [
    "code", "date", "name", "open", "high", "low", "close", "pre_close",
    "volume", "amount", "turnover_rate", "float_mv", "total_mv",
]
_SINA_SPOT_URL = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"


def _sina_symbol(code: str) -> str:
    c = str(code).strip()
    if c.startswith(("6", "9", "5")):
        return f"sh{c}"
    if c.startswith(("0", "2", "3", "1")):
        return f"sz{c}"
    if c.startswith(("4", "8")):
        return f"bj{c}"
    return f"sh{c}"


def fetch_spot_sina() -> pd.DataFrame:
    """新浪全市场实时行情 → daily_raw 13 列归一化（date 由调用方填当日）。

    新浪原始字段含 akshare ``stock_zh_a_spot`` 丢弃的 mktcap(总市值)/nmc(流通市值)/
    settlement(昨收)/turnoverratio(换手%)，故直连解析。
    """
    import requests

    base = {
        "sort": "symbol", "asc": 1, "node": "hs_a", "symbol": "",
        "num": 80, "page": 1, "_s_r_a": "page",
    }
    rows: list[dict] = []
    page = 1
    while True:
        base["page"] = page
        r = requests.get(_SINA_SPOT_URL, params=base, timeout=20)
        r.raise_for_status()
        txt = r.text.strip()
        if "=" in txt:
            txt = txt.split("=", 1)[1]
        txt = txt.rstrip(";").strip()
        if not txt:
            break
        data = json.loads(txt)
        if not isinstance(data, list) or not data:
            break
        rows.extend(data)
        if len(data) < 80:
            break
        page += 1
        time.sleep(1.0)

    if not rows:
        return pd.DataFrame(columns=_SPOT_COLUMNS)
    df = pd.DataFrame(rows)
    out = pd.DataFrame()
    out["code"] = df["code"].astype(str).str.strip().str.zfill(6)
    out["name"] = df["name"].fillna("").astype(str).str.strip()
    out["open"] = pd.to_numeric(df["open"], errors="coerce")
    out["high"] = pd.to_numeric(df["high"], errors="coerce")
    out["low"] = pd.to_numeric(df["low"], errors="coerce")
    out["close"] = pd.to_numeric(df["trade"], errors="coerce")
    out["pre_close"] = pd.to_numeric(df["settlement"], errors="coerce")
    out["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100  # 手 → 股
    out["amount"] = pd.to_numeric(df["amount"], errors="coerce") * 1e4  # 万元 → 元
    out["turnover_rate"] = pd.to_numeric(df["turnoverratio"], errors="coerce")
    out["float_mv"] = pd.to_numeric(df["nmc"], errors="coerce") * 1e4    # 万元 → 元
    out["total_mv"] = pd.to_numeric(df["mktcap"], errors="coerce") * 1e4
    return out


def fetch_hist_sina(code: str, *, start: str, end: str, adjust: str = "") -> pd.DataFrame:
    """新浪历史日 K（``stock_zh_a_daily``）→ daily_raw schema（无 name/pre_close/市值）。"""
    import akshare as ak

    df = _retry(
        lambda: ak.stock_zh_a_daily(
            symbol=_sina_symbol(code),
            start_date=start.replace("-", ""), end_date=end.replace("-", ""),
            adjust=adjust,
        ),
        label=f"hist_sina {code}", retries=3,
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
    for c in ("open", "high", "low", "close", "volume", "amount"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


class SinaDailySource:
    name = "sina"

    def fetch_spot(self) -> pd.DataFrame:
        return _retry(fetch_spot_sina, label="spot_sina", retries=2)

    def fetch_hist(self, code: str, *, start: str, end: str, adjust: str = "") -> pd.DataFrame:
        return fetch_hist_sina(code, start=start, end=end, adjust=adjust)

    # ---- 未实现接口：显式报错，不 fallback ----
    def fetch_index(self, code: str = "000300", *, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError("sina 未实现 fetch_index（可用 default/akshare）")

    def fetch_calendar(self) -> list[str]:
        raise NotImplementedError("sina 未实现 fetch_calendar（可用 default/akshare）")

    def fetch_code_list(self) -> list[str]:
        raise NotImplementedError("sina 未实现 fetch_code_list（可用 default/akshare）")

    def fetch_delisted_codes(self) -> pd.DataFrame:
        raise NotImplementedError("sina 未实现 fetch_delisted_codes（可用 default/akshare）")

    def fetch_delisted_daily(self, code: str, *, start: str, end: str) -> pd.DataFrame:
        raise NotImplementedError("sina 未实现 fetch_delisted_daily（可用 default/akshare）")
