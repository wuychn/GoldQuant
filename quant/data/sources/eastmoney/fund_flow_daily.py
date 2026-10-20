"""东财个股资金流日线。只取最近一段，避免整段历史把连接撑断。"""

from __future__ import annotations

import time

import requests

_URLS = (
    "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
    "https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get",
)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}
_FIELDS = (
    "日期",
    "主力净流入-净额",
    "小单净流入-净额",
    "中单净流入-净额",
    "大单净流入-净额",
    "超大单净流入-净额",
    "主力净流入-净占比",
    "小单净流入-净占比",
    "中单净流入-净占比",
    "大单净流入-净占比",
    "超大单净流入-净占比",
    "收盘价",
    "涨跌幅",
)


def _market(code: str) -> int:
    if code.startswith(("6", "9", "5")):
        return 1
    return 0


def fetch_fund_flow_daily(symbol: str, *, limit: int = 120) -> list[dict]:
    code = "".join(ch for ch in str(symbol) if ch.isdigit())
    code = code.zfill(6)[-6:] if code else ""
    if len(code) != 6:
        return []
    params = {
        "lmt": str(max(1, int(limit))),
        "klt": "101",
        "secid": f"{_market(code)}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": int(time.time() * 1000),
    }
    last: Exception | None = None
    payload = None
    for url in _URLS:
        try:
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            break
        except Exception as exc:
            last = exc
            payload = None
    if payload is None:
        raise RuntimeError(f"资金流日线失败 {code}") from last
    data = (payload or {}).get("data") or {}
    lines = data.get("klines") or []
    rows: list[dict] = []
    for line in lines:
        parts = str(line).split(",")
        if len(parts) < len(_FIELDS):
            continue
        row = dict(zip(_FIELDS, parts[: len(_FIELDS)]))
        row["main_net_inflow"] = row.get("主力净流入-净额")
        rows.append(row)
    return rows
