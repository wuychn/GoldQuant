"""东财严重异动列表（盘中实时）。临界涨幅用源站结果，不在本地重算。

接口：``https://dycalchis.eastmoney.com/price-anomaly/list``
字段与异动追踪中心一致：``t`` 临界涨幅(%)，``x`` 当前偏离(%)，``d`` 窗口天数，
``e`` 规则码，``o=2`` 为次日卡片。
北交所 ``s=6`` 且规则码为 4/5/6/7 时，展示码为原码乘 10（+150%/-60%/+300%/-75%）。
"""

from __future__ import annotations

from typing import Any

_URL = "https://dycalchis.eastmoney.com/price-anomaly/list"
_PARAMS = {
    "team": "h5",
    "product": "EastMoney",
    "client": "WAP",
    "version": "9001",
    "name": "WAP",
    "user": "123",
    "pageSize": 200,
    "pageNo": 1,
    "riseOnly": 0,
    "showBJS": 1,
}

TYPE_NAMES = {
    1: "10日4次同向",
    2: "10日3次同向",
    3: "10日3次同向",
    4: "10日偏离+100%",
    5: "10日偏离-50%",
    6: "30日偏离+200%",
    7: "30日偏离-70%",
    8: "10日3次同向",
    40: "10日偏离+150%",
    50: "10日偏离-60%",
    60: "30日偏离+300%",
    70: "30日偏离-75%",
}


def anomaly_type_key(board_flag: int, type_code: int) -> int:
    if int(board_flag) == 6 and int(type_code) in (4, 5, 6, 7):
        return int(type_code) * 10
    return int(type_code)


def parse_price_anomaly_payload(payload: dict | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in (payload or {}).get("data") or []:
        code = str(raw.get("c") or "").strip().zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        try:
            type_code = int(raw.get("e"))
            board = int(raw.get("s") or 0)
            key = anomaly_type_key(board, type_code)
            target = float(raw.get("t")) / 100.0
            change = float(raw.get("a")) / 100.0
            deviation = float(raw.get("x")) / 100.0
            days = int(raw.get("d") or 0)
            happen = int(raw.get("o") or 0)
        except (TypeError, ValueError):
            continue
        hit = change + 1e-12 >= target if target >= 0 else change <= target + 1e-12
        out.append(
            {
                "code": code,
                "name": str(raw.get("n") or ""),
                "days": days,
                "deviation": deviation,
                "target_ret": target,
                "change_ret": change,
                "type_key": key,
                "type_name": TYPE_NAMES.get(key, f"严重异常波动({key})"),
                "session": "next" if happen == 2 else "today",
                "hit": hit,
            }
        )
    return out


def fetch_severe_anomaly() -> list[dict[str, Any]]:
    import requests

    from common.utils.source_headers import load_headers_from_file

    resp = requests.get(_URL, params=_PARAMS, headers=load_headers_from_file(), timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    if int(payload.get("result") or 0) != 0:
        raise RuntimeError(str(payload.get("msg") or "price-anomaly 失败"))
    return parse_price_anomaly_payload(payload)
