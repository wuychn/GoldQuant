"""晚间候选池：三来源独立进入，已在 API 层完成初筛 + 问财 + enrich。"""

from __future__ import annotations

from quant.pool.candidate_config import (
    PAYLOAD_KEY_POPULARITY,
    PAYLOAD_KEY_ZT,
    THS_RANK_PAYLOAD_KEYS,
    include_ths_rank_pool,
    load_candidate_config,
)
from quant.pool.ths_rank_util import attach_ths_rank_tags, build_ths_rank_tag_map, stock_ths_rank_tags


def _code(row: dict) -> str:
    return str(row.get("股票代码") or row.get("代码") or "").strip()


def _merge_source_label(existing: dict, row: dict) -> None:
    label = str(row.get("候选来源") or "").strip()
    if not label:
        return
    prev = existing.get("候选来源")
    if not prev:
        existing["候选来源"] = label
        return
    if isinstance(prev, list):
        if label not in prev:
            prev.append(label)
        return
    if prev != label:
        existing["候选来源"] = [prev, label]


def build_candidates(payload: dict) -> list[dict]:
    """合并三来源候选（payload 中应已通过通用漏斗）。"""
    cfg = load_candidate_config()
    if not bool(cfg.get("include_zt_pool", False)):
        zt_key = None
    else:
        zt_key = PAYLOAD_KEY_ZT

    ths_keys = list(THS_RANK_PAYLOAD_KEYS) if include_ths_rank_pool(cfg) else []

    all_ths_rows: list[dict] = []
    for key in ths_keys:
        all_ths_rows.extend(payload.get(key) or [])
    tag_map = build_ths_rank_tag_map(all_ths_rows)

    source_keys = [PAYLOAD_KEY_POPULARITY]
    if zt_key:
        source_keys.append(zt_key)
    source_keys.extend(ths_keys)

    merged: dict[str, dict] = {}
    for key in source_keys:
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = _code(row)
            if not code:
                continue
            tagged = attach_ths_rank_tags(dict(row), tag_map)
            if code in merged:
                _merge_source_label(merged[code], tagged)
                combined = dict(merged[code])
                for k, v in tagged.items():
                    if k != "候选来源":
                        combined[k] = v
                merged[code] = attach_ths_rank_tags(combined, tag_map)
            else:
                merged[code] = tagged

    out: list[dict] = []
    for row in merged.values():
        tags = stock_ths_rank_tags(row)
        if tags and not row.get("榜单标签"):
            row = {**row, "榜单标签": tags}
        out.append(row)
    return out
