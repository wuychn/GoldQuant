"""东财龙虎榜当日明细：``stock_lhb_detail_em``。一只票可有多条上榜原因。"""

from __future__ import annotations

import pandas as pd

LHB_COLUMNS = (
    "code",
    "date",
    "name",
    "reason",
    "close",
    "pct",
    "net_buy",
    "buy_amt",
    "sell_amt",
    "lhb_amount",
    "mkt_amount",
    "net_ratio",
    "turnover",
    "on_list",
)

_RENAME = {
    "代码": "code",
    "名称": "name",
    "上榜日": "date",
    "上榜原因": "reason",
    "解读": "note",
    "收盘价": "close",
    "涨跌幅": "pct",
    "龙虎榜净买额": "net_buy",
    "龙虎榜买入额": "buy_amt",
    "龙虎榜卖出额": "sell_amt",
    "龙虎榜成交额": "lhb_amount",
    "市场总成交额": "mkt_amount",
    "净买额占总成交比": "net_ratio",
    "换手率": "turnover",
}


def _code6(s) -> str:
    digits = "".join(ch for ch in str(s or "") if ch.isdigit())
    return digits.zfill(6)[-6:] if digits else ""


def normalize_lhb(df: pd.DataFrame | None, *, as_of: str) -> pd.DataFrame:
    empty = pd.DataFrame(columns=list(LHB_COLUMNS))
    if df is None or df.empty:
        return empty
    d = df.copy().rename(columns=_RENAME)
    if "code" not in d.columns:
        return empty
    d["code"] = d["code"].map(_code6)
    d = d[d["code"].str.len() == 6]
    if "date" not in d.columns:
        d["date"] = as_of
    d["date"] = pd.to_datetime(d["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    d["date"] = d["date"].fillna(as_of)
    d = d[d["date"] == as_of]
    if "reason" not in d.columns:
        d["reason"] = d["note"] if "note" in d.columns else ""
    d["reason"] = d["reason"].fillna("").astype(str)
    if "name" not in d.columns:
        d["name"] = ""
    d["on_list"] = True
    for c in LHB_COLUMNS:
        if c not in d.columns:
            d[c] = pd.NA
    for c in ("close", "pct", "net_buy", "buy_amt", "sell_amt", "lhb_amount", "mkt_amount", "net_ratio", "turnover"):
        s = d[c]
        if s.dtype == object:
            s = s.astype(str).str.replace("%", "", regex=False).str.replace(",", "", regex=False)
        d[c] = pd.to_numeric(s, errors="coerce")
    return d[list(LHB_COLUMNS)].reset_index(drop=True)


def fetch_lhb_day(*, date: str) -> pd.DataFrame:
    import akshare as ak

    as_of = str(date).strip()[:10]
    ymd = as_of.replace("-", "")
    df = ak.stock_lhb_detail_em(start_date=ymd, end_date=ymd)
    return normalize_lhb(df, as_of=as_of)
