"""DailySource 实现共享工具：重试退避 + 东财 kline 直连。

从原 ``quant/data/fetch.py`` 搬出，避免 fetch.py(facade) ↔ daily(实现) 的循环 import。
"""

from __future__ import annotations

import time

import pandas as pd

from quant.data.schema import INDEX_DAILY_COLUMNS

# 限流标记（异常消息命中则退避加倍）
_LIMIT_MARKERS = ("429", "限流", "too many", "rate limit", "ratelimit", "throttl")


def _retry(fn, *, retries: int = 4, base: float = 1.0, label: str = "", empty_ok: bool = False):
    """指数退避重试：空数据或异常都重试；限流（异常消息含标记）退避加倍。

    第 i 次失败后 sleep ``base * 2**i``（1/2/4/8s），命中限流标记再 ×2。
    成功返回非空 df；重试耗尽：
      - 末次为真实异常 → 抛出
      - 末次仅为空数据且 ``empty_ok=True`` → 返回空 DataFrame（供 hist 走退市回退）
      - 末次为空且 ``empty_ok=False`` → 抛 ``返回空数据``
    """
    last_exc: Exception | None = None
    last_was_empty = False
    for i in range(retries + 1):
        try:
            df = fn()
            if df is not None and not df.empty:
                return df
            last_was_empty = True
            last_exc = RuntimeError(f"{label or 'fetch'} 返回空数据")
        except Exception as e:  # noqa: BLE001
            last_was_empty = False
            last_exc = e
        if i >= retries:
            break
        msg = str(last_exc).lower()
        mult = 2.0 if any(m in msg for m in _LIMIT_MARKERS) else 1.0
        time.sleep(base * (2 ** i) * mult)
    assert last_exc is not None
    if empty_ok and last_was_empty:
        return pd.DataFrame()
    raise last_exc


def _index_secid(code: str) -> str:
    """东财 secid：沪市指数 ``1.<code>``；深市 399xxx 用 ``0.<code>``。"""
    c = str(code).strip()
    if c.startswith("399"):
        return f"0.{c}"
    return f"1.{c}"


def eastmoney_index_kline(code: str, *, start: str, end: str) -> pd.DataFrame:
    """东财 push2his kline 拉指数日线 → index_daily schema（curl_cffi 补丁自动注入头）。

    替代 akshare ``index_zh_a_hist``（先走 80.push2/clist 易被 TLS 指纹反爬断开）；
    kline 接口带 ``.eastmoney.header`` 稳定。
    """
    import requests

    from common.utils.source_headers import load_headers_from_file

    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": _index_secid(code),
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
        # 用 dict 构造：标量 code 广播到 N 行；勿用 out["code"]=scalar 在空帧上赋值
        # （此时 out 0 行，后续 Series 赋值展开到 N 行时 code 列退化为 NaN，
        #  曾导致 read_index_daily 按 code 过滤返回空 → 回测基准/超额全 0）。
        out = pd.DataFrame(
            {
                "code": str(code),
                "date": raw["date"],
                "open": raw["open"],
                "high": raw["high"],
                "low": raw["low"],
                "close": raw["close"],
                "volume": raw["volume"],
                "amount": raw["amount"],
            }
        )
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
        for c in ("open", "high", "low", "close", "volume", "amount"):
            out[c] = pd.to_numeric(out[c], errors="coerce")
        return out[list(INDEX_DAILY_COLUMNS)]

    return _retry(_call, label=f"index {code}")
