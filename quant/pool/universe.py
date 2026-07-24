"""候选 Universe：eligible 板块下的个股。"""

from __future__ import annotations

from quant.io.payload import popularity_rows, stock_rows
from quant.scoring.industry_aliases import expand_industries


def _parse_name_set(raw: object) -> set[str]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text in ("无", "-", "—"):
            return set()
        return {x.strip() for x in text.replace(";", "、").replace(",", "、").split("、") if x.strip()}
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    return set()


def _row_concepts(row: dict) -> set[str]:
    return _parse_name_set(row.get("所属概念") or row.get("概念"))


def _row_industries(row: dict) -> set[str]:
    return expand_industries(_parse_name_set(row.get("行业")))


def scan_universe(payload: dict, eligible_sectors: set[str]) -> list[dict]:
    if not eligible_sectors:
        return []
    keys = (
        "同花顺人气榜",
        "人气榜",
        "创新高",
        "持续上涨",
        "持续放量",
        "量价齐升",
    )
    rows = stock_rows(payload, *keys)
    if not rows:
        rows = popularity_rows(payload)

    out: list[dict] = []
    for row in rows:
        tags = _row_concepts(row) | _row_industries(row)
        if not tags & eligible_sectors:
            continue
        hit = sorted(tags & eligible_sectors)
        enriched = dict(row)
        enriched["sector_tags"] = hit
        out.append(enriched)
    return out
