"""晚间候选池：三来源独立进入，已在 API 层完成初筛 + 问财 + enrich。"""

from __future__ import annotations

from quant.pool.candidate_config import (
    PAYLOAD_KEY_PKYD,
    PAYLOAD_KEY_POPULARITY,
    PAYLOAD_KEY_ZT,
    load_candidate_config,
)
from quant.pool.pkyd_util import attach_pkyd_tags, build_pkyd_tag_map, stock_pkyd_tags


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
    if not bool(cfg.get("include_zt_pool", True)):
        zt_key = None
    else:
        zt_key = PAYLOAD_KEY_ZT
    if not bool(cfg.get("include_pkyd_pool", True)):
        pkyd_key = None
    else:
        pkyd_key = PAYLOAD_KEY_PKYD

    tag_map = build_pkyd_tag_map(payload.get(PAYLOAD_KEY_PKYD))
    source_keys = [PAYLOAD_KEY_POPULARITY]
    if zt_key:
        source_keys.append(zt_key)
    if pkyd_key:
        source_keys.append(pkyd_key)

    merged: dict[str, dict] = {}
    for key in source_keys:
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            code = _code(row)
            if not code:
                continue
            tagged = attach_pkyd_tags(dict(row), tag_map)
            if code in merged:
                _merge_source_label(merged[code], tagged)
                combined = dict(merged[code])
                for k, v in tagged.items():
                    if k != "候选来源":
                        combined[k] = v
                merged[code] = attach_pkyd_tags(combined, tag_map)
            else:
                merged[code] = tagged

    out: list[dict] = []
    for row in merged.values():
        tags = stock_pkyd_tags(row)
        if tags and not row.get("盘口异动标签"):
            row = {**row, "盘口异动标签": tags}
        out.append(row)
    return out
