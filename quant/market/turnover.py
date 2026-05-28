"""全市场成交额解析与历史归档读取。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from quant.store.paths import _SH_TZ, daily_raw

_TURNOVER_YI_RE = re.compile(r"([\d.]+)")


def parse_turnover_yi(value: object) -> float | None:
    """解析 ``16834.38亿`` → ``16834.38``。"""
    if value is None:
        return None
    m = _TURNOVER_YI_RE.search(str(value).strip().replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def turnover_from_payload(payload: dict) -> float | None:
    """从 ``赚钱效应`` 提取全市场成交额（亿）。"""
    profit = payload.get("赚钱效应") or {}
    if not isinstance(profit, dict):
        return None
    block = profit.get("成交额")
    if isinstance(block, dict):
        for key in ("今日全天", "今日累计"):
            v = parse_turnover_yi(block.get(key))
            if v is not None:
                return v
    for key in ("今日成交额", "今日全天", "今日累计"):
        v = parse_turnover_yi(profit.get(key))
        if v is not None:
            return v
    return None


def load_completed_day_turnovers(*, count: int) -> list[float]:
    """读取最近 ``count`` 个**已归档收盘日**的全天成交额（亿），按时间升序。

    数据来源：``~/.quant/daily/{date}/raw/evening.json``。
    不含当日盘中未完成数据。
    """
    if count <= 0:
        return []
    found: list[tuple[str, float]] = []
    d = datetime.now(_SH_TZ).date() - timedelta(days=1)
    for _ in range(45):
        if len(found) >= count:
            break
        ds = d.strftime("%Y-%m-%d")
        path: Path = daily_raw("evening.json", ds)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                amt = turnover_from_payload(data if isinstance(data, dict) else {})
                if amt is not None:
                    found.append((ds, amt))
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        d -= timedelta(days=1)
    found.sort(key=lambda x: x[0])
    return [v for _, v in found[-count:]]
