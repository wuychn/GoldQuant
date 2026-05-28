"""评分上下文：封装单次 quant 运行的 API payload。"""

from __future__ import annotations

from dataclasses import dataclass


def _zt_block(payload: dict) -> dict:
    """兼容盘前「涨停概况」与盘中/盘后「涨停统计」两种字段名。"""
    block = payload.get("涨停统计") or payload.get("涨停概况") or {}
    return block if isinstance(block, dict) else {}


def zt_pool(payload: dict) -> list[dict]:
    block = _zt_block(payload)
    pool = block.get("今日涨停") or []
    return pool if isinstance(pool, list) else []


def find_in_zt_pool(code: str, payload: dict) -> dict | None:
    code = str(code).strip()
    for row in zt_pool(payload):
        c = str(row.get("代码") or row.get("股票代码") or "").strip()
        if c == code:
            return row
    return None


def zt_height(payload: dict) -> int:
    """市场最高连板数（涨停统计.市场高度 或池内最大连板数）。"""
    block = _zt_block(payload)
    raw = block.get("市场高度", "")
    if isinstance(raw, str):
        digits = "".join(ch for ch in raw if ch.isdigit())
        if digits:
            return int(digits)
    mx = 0
    for row in zt_pool(payload):
        try:
            mx = max(mx, int(float(row.get("连板数", 0) or 0)))
        except (TypeError, ValueError):
            continue
    return mx


def index_change(payload: dict, code: str = "000001") -> float | None:
    """上证指数涨跌幅(%) — 来自大盘指数。"""
    for row in payload.get("大盘指数") or []:
        if str(row.get("代码", "")).strip() == code:
            try:
                return float(row.get("涨跌幅"))
            except (TypeError, ValueError):
                return None
    return None


def profit_effect(payload: dict) -> dict:
    """赚钱效应块（上涨/下跌/涨停家数等）。"""
    block = payload.get("赚钱效应") or {}
    return block if isinstance(block, dict) else {}


def infer_regime(payload: dict) -> str:
    """内部用：由大盘指数/赚钱效应/涨停统计推断仓位与三确认间隔档位（强势/震荡/弱势）。"""
    profit = profit_effect(payload)
    up = int(profit.get("上涨", 0) or 0)
    down = int(profit.get("下跌", 0) or 0)
    zt_cnt = int(profit.get("涨停", len(zt_pool(payload))) or len(zt_pool(payload)))
    idx_chg = index_change(payload)

    votes_strong = votes_weak = 0
    if idx_chg is not None:
        if idx_chg > 0.5:
            votes_strong += 1
        elif idx_chg < -0.5:
            votes_weak += 1
    if up > down:
        votes_strong += 1
    elif up < down:
        votes_weak += 1
    if zt_cnt >= 60:
        votes_strong += 1
    elif zt_cnt < 30:
        votes_weak += 1
    height = zt_height(payload)
    if height >= 5:
        votes_strong += 1
    elif height <= 2:
        votes_weak += 1

    if votes_strong >= 4 and idx_chg is not None and idx_chg >= 0.5 and zt_cnt >= 50:
        return "强势"
    if votes_weak >= 3:
        return "弱势"
    return "震荡"


@dataclass
class ScoreContext:
    """单次运行的评分/门禁上下文。"""

    payload: dict
    mode: str = ""

    @classmethod
    def from_payload(cls, payload: dict, *, mode: str = "") -> ScoreContext:
        return cls(payload=payload, mode=mode)
