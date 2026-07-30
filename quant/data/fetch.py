"""AKShare 行情拉取与字段归一化（供 build/update/maintain 脚本调用）。

把 spot_em / stock_zh_a_hist / index_zh_a_hist 的返回归一化到 daily_raw / index_daily schema。
所有 akshare 调用经 ``_retry`` 包裹：指数退避 + 限流识别，避免被东财限流直接失败。
"""

from __future__ import annotations

import time

import pandas as pd

from quant.data.schema import HIST_FIELD_MAP, INDEX_DAILY_COLUMNS, SPOT_EM_FIELD_MAP

# 进程级注入东财请求头（.eastmoney.header 里的 Cookie/user-agent）。
# build/update/maintain 与 ``python -m quant`` 直跑不经 app lifespan，故在数据拉取模块
# 加载时统一打补丁，否则 requests 裸奔会被东财识别为爬虫断开（RemoteDisconnected）。
from common.utils.source_headers import apply_source_header_patch

apply_source_header_patch()

# 限流标记（异常消息命中则退避加倍）
_LIMIT_MARKERS = ("429", "限流", "too many", "rate limit", "ratelimit", "throttl")


def _retry(fn, *, retries: int = 4, base: float = 1.0, label: str = ""):
    """指数退避重试：空数据或异常都重试；限流（异常消息含标记）退避加倍。

    第 i 次失败后 sleep ``base * 2**i``（1/2/4/8s），命中限流标记再 ×2。
    成功返回非空 df；重试耗尽抛最后一个异常。
    """
    last_exc: Exception | None = None
    for i in range(retries + 1):
        try:
            df = fn()
            if df is not None and not df.empty:
                return df
            last_exc = RuntimeError(f"{label or 'fetch'} 返回空数据")
        except Exception as e:  # noqa: BLE001
            last_exc = e
        if i >= retries:
            break
        msg = str(last_exc).lower()
        mult = 2.0 if any(m in msg for m in _LIMIT_MARKERS) else 1.0
        time.sleep(base * (2 ** i) * mult)
    assert last_exc is not None
    raise last_exc


def fetch_spot_em() -> pd.DataFrame:
    """全市场实时行情 → daily_raw 列（不含 date，由调用方补当日）。"""
    import akshare as ak

    df = _retry(lambda: ak.stock_zh_a_spot_em(), label="spot_em")
    return _normalize_spot(df)


def _normalize_spot(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=list(SPOT_EM_FIELD_MAP.values()))
    out = pd.DataFrame()
    for src, dst in SPOT_EM_FIELD_MAP.items():
        if src in df.columns:
            out[dst] = df[src]
    for c in ("open", "high", "low", "close", "pre_close", "volume", "amount",
              "turnover_rate", "float_mv", "total_mv", "pct", "vol_ratio", "speed"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    out["code"] = out["code"].astype(str).str.strip()
    return out


def fetch_a_code_name() -> list[str]:
    """A 股代码表（``stock_info_a_code_name``，东财 stockapi，不走 clist，稳定）。

    替代 ``stock_zh_a_spot_em`` 拿代码表：后者走 ``push2/clist`` 反爬严易被断
    （RemoteDisconnected）；stockapi 接口稳定。盘中实时快照仍用 ``fetch_spot_em``。
    """
    import akshare as ak

    df = _retry(lambda: ak.stock_info_a_code_name(), label="a_code_name", retries=2)
    return df["code"].astype(str).str.strip().tolist()


def fetch_hist(code: str, *, start: str, end: str, adjust: str = "") -> pd.DataFrame:
    """单只历史日线 → daily_raw 列（含 date）。``adjust`` 默认不复权。"""
    import akshare as ak

    df = _retry(
        lambda: ak.stock_zh_a_hist(
            symbol=str(code),
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=adjust,
        ),
        label=f"hist {code}",
        retries=3,
    )
    return _normalize_hist(df, code)


def _normalize_hist(df: pd.DataFrame, code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "date", "name", "open", "high", "low",
                                      "close", "volume", "amount", "turnover_rate"])
    out = pd.DataFrame()
    for src, dst in HIST_FIELD_MAP.items():
        if src in df.columns:
            out[dst] = df[src]
    if "code" not in out.columns or out["code"].isna().any():
        out["code"] = str(code).strip()
    out["code"] = out["code"].astype(str).str.strip()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for c in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def fetch_index(code: str = "000300", *, start: str, end: str) -> pd.DataFrame:
    """指数日线 → index_daily 列。

    直接调东财 kline 接口（push2his）拉取。akshare 的 ``index_zh_a_hist`` 先走
    ``80.push2/clist`` 易被东财限流/断开（RemoteDisconnected）；kline 接口带
    ``.eastmoney.header``（补丁自动注入）稳定。
    """
    import requests

    from common.utils.source_headers import load_headers_from_file

    secid = f"1.{code}"  # 沪市指数前缀（沪深300/中证500/中证1000 均为 1.）
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": "101",   # 日线
        "fqt": "0",     # 不复权
        "beg": start.replace("-", ""),
        "end": end.replace("-", ""),
    }

    def _call() -> pd.DataFrame:
        resp = requests.get(url, params=params, headers=load_headers_from_file(), timeout=15)
        resp.raise_for_status()
        kls = (resp.json().get("data") or {}).get("klines") or []
        if not kls:
            return pd.DataFrame(columns=list(INDEX_DAILY_COLUMNS))
        # 每行: date,open,close,high,low,volume,amount,amplitude
        rows = [k.split(",") for k in kls]
        raw = pd.DataFrame(rows, columns=["date", "open", "close", "high", "low", "volume", "amount", "_amp"])
        out = pd.DataFrame()
        out["code"] = str(code)
        out["date"] = raw["date"]
        out["open"] = raw["open"]
        out["high"] = raw["high"]
        out["low"] = raw["low"]
        out["close"] = raw["close"]
        out["volume"] = raw["volume"]
        out["amount"] = raw["amount"]
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
        for c in ("open", "high", "low", "close", "volume", "amount"):
            out[c] = pd.to_numeric(out[c], errors="coerce")
        return out[list(INDEX_DAILY_COLUMNS)]

    return _retry(_call, label=f"index {code}")


def fetch_trade_calendar() -> list[str]:
    import akshare as ak

    df = _retry(lambda: ak.tool_trade_date_hist_sina(), label="calendar")
    col = None
    for c in df.columns:
        if "date" in str(c).lower() or "日期" in str(c):
            col = c
            break
    if col is None:
        return []
    return sorted(pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d").tolist())
