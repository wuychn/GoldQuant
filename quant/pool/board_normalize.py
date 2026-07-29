"""Industry board row normalization (shared by payload merge)."""

from __future__ import annotations

from typing import Any


def normalize_industry_board_row(row: dict[str, Any]) -> dict[str, Any]:
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
    for k in ("序号", "领涨股", "领涨股-涨跌幅", "当前价"):
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
