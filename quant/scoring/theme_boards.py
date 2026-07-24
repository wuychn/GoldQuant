"""概念板块、行业板块读取（评分分轨，不做跨域合并）。"""

from __future__ import annotations

from typing import Any

BOARD_CONCEPT = "概念板块"
BOARD_INDUSTRY = "行业板块"


def normalize_industry_board_row(row: dict[str, Any]) -> dict[str, Any]:
    """同花顺行业一览表 → 与概念榜一致的字段（行业 / 行业-涨跌幅 / 净额）。"""
    name = str(row.get("板块") or row.get("行业") or "").strip()
    if not name:
        return {}
    chg = row.get("行业-涨跌幅")
    if chg is None:
        chg = row.get("涨跌幅")
    net = row.get("净额")
    if net is None:
        net = row.get("净流入")
    out: dict[str, Any] = {
        "行业": name,
        "行业-涨跌幅": chg,
        "净额": net,
        "题材来源": "行业",
    }
    for k in ("序号", "领涨股", "领涨股-涨跌幅", "当前价", "上涨家数", "下跌家数", "公司家数"):
        if k in row:
            out[k] = row[k]
    return out


def normalize_industry_board_rows(rows: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        norm = normalize_industry_board_row(row)
        if norm:
            out.append(norm)
    return out


def section_board_rows(
    payload: dict,
    section: str,
    key: str,
    *,
    limit: int = 10,
) -> list[dict]:
    """读取概念板块或行业板块的单榜行（不跨域合并）。"""
    block = payload.get(section) or {}
    if not isinstance(block, dict):
        block = {}
    rows = block.get(key) or []
    if section == BOARD_INDUSTRY:
        rows = normalize_industry_board_rows(rows if isinstance(rows, list) else None)
    else:
        rows = [r for r in (rows or []) if isinstance(r, dict)][:limit]
    return [r for r in rows if isinstance(r, dict)][:limit]


def _theme_name(row: dict[str, Any]) -> str:
    return str(row.get("行业") or row.get("板块") or "").strip()


def _theme_names_from_rows(rows: list[dict] | None, *, limit: int = 10) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = _theme_name(row)
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= limit:
            break
    return names


def board_gain_fund_lists(
    payload: dict,
    section: str,
    *,
    limit: int = 10,
) -> tuple[list[str], list[str]]:
    """从 payload 的「概念板块」或「行业板块」读取涨幅榜/资金流入榜名称。"""
    gain = _theme_names_from_rows(
        section_board_rows(payload, section, "涨幅榜", limit=limit),
        limit=limit,
    )
    fund = _theme_names_from_rows(
        section_board_rows(payload, section, "资金流入榜", limit=limit),
        limit=limit,
    )
    return gain, fund
