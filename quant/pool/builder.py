"""晚间复盘候选池：人气榜 TopN ∪ 涨停池今日涨停（去重）。

仅 post_market_evening 加自选时使用；盘中/盘前只交易已在 state/optional.jsonl 中的标的。
"""

from __future__ import annotations

from quant.config import load_scoring_config


def _code(row: dict) -> str:
    return str(row.get("股票代码") or row.get("代码") or "").strip()


def _name(row: dict) -> str:
    return str(row.get("股票名称") or row.get("名称") or "").strip()


def build_candidates(payload: dict) -> list[dict]:
    cfg = load_scoring_config().get("candidate") or {}
    limit = int(cfg.get("popularity_limit", 20))
    include_zt = bool(cfg.get("include_zt_pool", True))

    merged: dict[str, dict] = {}

    for row in (payload.get("同花顺人气榜") or [])[:limit]:
        if not isinstance(row, dict):
            continue
        code = _code(row)
        if not code:
            continue
        merged[code] = dict(row)

    if include_zt:
        zt = payload.get("涨停统计") or payload.get("涨停概况") or {}
        pool = zt.get("今日涨停") if isinstance(zt, dict) else []
        for row in pool or []:
            if not isinstance(row, dict):
                continue
            code = _code(row)
            if not code:
                continue
            if code in merged:
                merged[code].update({k: v for k, v in row.items() if v not in (None, "")})
            else:
                merged[code] = {
                    "股票代码": code,
                    "股票名称": _name(row),
                    **row,
                }

    return list(merged.values())
