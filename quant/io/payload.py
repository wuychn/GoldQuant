"""Payload 字段抽取（无业务逻辑）。"""

from __future__ import annotations

from typing import Any

from quant.scoring.context import (
    index_change,
    infer_regime,
    profit_effect,
    zt_height,
    zt_pool,
)
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY, section_board_rows


def unwrap_data(raw: dict) -> dict:
    inner = raw.get("data")
    return inner if isinstance(inner, dict) else raw


def stock_rows(payload: dict, *keys: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for key in keys:
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("股票代码") or row.get("代码") or "").strip()
            if not code or code in seen:
                continue
            seen.add(code)
            out.append(row)
    return out


def combat_watchlist(payload: dict) -> list[dict]:
    return stock_rows(payload, "自选股", "持仓股")


def popularity_rows(payload: dict) -> list[dict]:
    for key in ("同花顺人气榜", "人气榜"):
        rows = payload.get(key)
        if isinstance(rows, list) and rows:
            return [r for r in rows if isinstance(r, dict)]
    return []


def board_sections(payload: dict) -> tuple[list[dict], list[dict]]:
    concept_gain = section_board_rows(payload, BOARD_CONCEPT, "涨幅榜", limit=50)
    concept_fund = section_board_rows(payload, BOARD_CONCEPT, "资金流入榜", limit=50)
    industry_gain = section_board_rows(payload, BOARD_INDUSTRY, "涨幅榜", limit=50)
    industry_fund = section_board_rows(payload, BOARD_INDUSTRY, "资金流入榜", limit=50)
    return concept_gain + concept_fund, industry_gain + industry_fund


def market_snapshot(payload: dict) -> dict[str, Any]:
    pe = profit_effect(payload)
    return {
        "regime_label": infer_regime(payload),
        "zt_count": int(pe.get("涨停") or len(zt_pool(payload)) or 0),
        "up_count": int(pe.get("上涨") or 0),
        "down_count": int(pe.get("下跌") or 0),
        "index_chg": index_change(payload),
        "zt_height": zt_height(payload),
    }
