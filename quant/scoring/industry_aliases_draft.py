"""自动生成东财 jbxx「行业」→ 同花顺「板块」映射，写回包内配置文件。

输出：quant/config/industry_aliases.yml
元数据：~/.quant/cache/industry_aliases_draft_meta.json

用法：python -m quant industry_aliases_draft
"""

from __future__ import annotations

import json
import logging
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.utils.dataframe import dataframe_to_records
from quant.scoring.industry_aliases import package_industry_aliases_path, reload_industry_aliases_cache
from quant.store.paths import ensure_layout, quant_cache_file
from quant.timeutil import cn_datetime_str

logger = logging.getLogger(__name__)

_META_NAME = "industry_aliases_draft_meta.json"
_ROMAN_SUFFIX = re.compile(r"[ⅡⅢⅣIV]+$")
_STRIP_SUFFIXES = ("行业", "板块")

# jbxx 缓存与同花顺板块核对后仍无法自动匹配的兜底（勿凭常识乱填）
_MANUAL_HINTS: dict[str, str] = {
    "休闲食品": "食品加工制造",
    "玻璃玻纤": "非金属材料",
    "工程咨询服务Ⅱ": "其他社会服务",
    "工程咨询服务": "其他社会服务",
}


def normalize_industry_name(name: str) -> str:
    s = str(name).strip()
    s = _ROMAN_SUFFIX.sub("", s)
    for suffix in _STRIP_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix) + 1:
            s = s[: -len(suffix)]
    return s.strip()


def fetch_ths_industry_names(*, retries: int = 3) -> list[str]:
    import akshare as ak

    last_err: Exception | None = None
    for i in range(max(1, retries)):
        try:
            rows = dataframe_to_records(ak.stock_board_industry_summary_ths())
            names = sorted(
                {
                    str(r.get("板块", "")).strip()
                    for r in rows
                    if str(r.get("板块", "")).strip()
                }
            )
            if names:
                return names
        except Exception as exc:
            last_err = exc
            logger.warning("拉取同花顺行业榜失败 retry=%d err=%s", i + 1, exc)
            time.sleep(2)
    raise RuntimeError("无法拉取同花顺行业一览表") from last_err


def fetch_em_board_names(*, retries: int = 3) -> list[str]:
    import akshare as ak

    last_err: Exception | None = None
    for i in range(max(1, retries)):
        try:
            rows = dataframe_to_records(ak.stock_board_industry_name_em())
            names = sorted(
                {
                    str(r.get("板块名称", "")).strip()
                    for r in rows
                    if str(r.get("板块名称", "")).strip()
                }
            )
            if names:
                return names
        except Exception as exc:
            last_err = exc
            logger.warning("拉取东财行业板块列表失败 retry=%d err=%s", i + 1, exc)
            time.sleep(2)
    logger.warning("东财行业板块列表不可用，将仅使用 jbxx 缓存等行业名")
    return []


def load_jbxx_industry_names() -> list[str]:
    path = quant_cache_file("stock_jbxx.json")
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    names: set[str] = set()
    stocks = raw.get("stocks") if isinstance(raw, dict) else None
    if not isinstance(stocks, dict):
        return []
    for entry in stocks.values():
        if not isinstance(entry, dict):
            continue
        data = entry.get("data")
        if not isinstance(data, dict):
            continue
        name = str(data.get("行业", "")).strip()
        if name and name not in ("无", "-", "—"):
            names.add(name)
    return sorted(names)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def propose_ths_match(
    em_name: str,
    ths_names: list[str],
    ths_set: set[str],
) -> tuple[str | None, str, float]:
    em = em_name.strip()
    if not em:
        return None, "empty", 0.0
    if em in ths_set:
        return None, "exact", 1.0

    norm = normalize_industry_name(em)
    if norm in ths_set:
        return norm, "normalize", 0.95

    substring_hits: list[str] = []
    for ths in ths_names:
        ths_norm = normalize_industry_name(ths)
        if len(ths) < 2:
            continue
        if ths in em or em in ths or ths_norm in norm or norm in ths_norm:
            substring_hits.append(ths)
    if len(substring_hits) == 1:
        return substring_hits[0], "substring", 0.88
    if len(substring_hits) > 1:
        contained = [t for t in substring_hits if t in em or normalize_industry_name(t) in norm]
        if len(contained) == 1:
            return contained[0], "substring", 0.86
        return None, "ambiguous_substring", 0.0

    best_ths: str | None = None
    best_score = 0.0
    for ths in ths_names:
        score = max(_ratio(norm, normalize_industry_name(ths)), _ratio(em, ths))
        if score > best_score:
            best_score = score
            best_ths = ths
    if best_ths and best_score >= 0.72:
        return best_ths, "fuzzy", best_score
    return None, "unmapped", best_score


def build_alias_draft(
    ths_names: list[str],
    em_names: list[str],
    *,
    min_score: float = 0.72,
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    ths_set = set(ths_names)
    ths_to_em: dict[str, list[str]] = {}
    mapping_detail: list[dict[str, Any]] = []
    unmapped: list[str] = []
    exact_matches: list[str] = []

    for em in sorted(set(em_names)):
        ths, method, score = propose_ths_match(em, ths_names, ths_set)
        if ths is None and em in _MANUAL_HINTS:
            hint = _MANUAL_HINTS[em]
            if hint in ths_set and em != hint:
                ths, method, score = hint, "manual_hint", 0.99
        if method == "exact":
            exact_matches.append(em)
            continue
        if ths is None or score < min_score:
            unmapped.append(em)
            mapping_detail.append(
                {"em": em, "ths": None, "method": method, "score": round(score, 3)}
            )
            continue
        if em == ths:
            exact_matches.append(em)
            continue
        ths_to_em.setdefault(ths, [])
        if em not in ths_to_em[ths]:
            ths_to_em[ths].append(em)
        mapping_detail.append(
            {"em": em, "ths": ths, "method": method, "score": round(score, 3)}
        )

    for ths in ths_to_em:
        ths_to_em[ths].sort()

    meta = {
        "generated_at": cn_datetime_str(),
        "ths_count": len(ths_names),
        "em_input_count": len(set(em_names)),
        "alias_groups": len(ths_to_em),
        "alias_entries": sum(len(v) for v in ths_to_em.values()),
        "exact_matches": sorted(exact_matches),
        "unmapped_em": sorted(unmapped),
        "mappings": mapping_detail,
    }
    return ths_to_em, meta


def _yaml_dump_aliases(aliases: dict[str, list[str]], meta: dict[str, Any]) -> str:
    lines = [
        "# 行业名称映射（仅行业域；概念不做映射，概念与行业互不交叉）",
        "#",
        "# 编写约定（aliases 块）：",
        "#   键 key   → 同花顺：行业一览表 hyylb「板块」名（行业榜 canonical，评分数据侧）",
        "#   列表项   → 东财：jbxx「行业」或东财行业板块名（个股 enrich 侧）",
        "#",
        f"# generated_at: {meta.get('generated_at', '')}",
        f"# ths={meta.get('ths_count', 0)} em_input={meta.get('em_input_count', 0)} "
        f"aliases={meta.get('alias_entries', 0)} unmapped={len(meta.get('unmapped_em') or [])}",
        "# 刷新：python -m quant industry_aliases_draft",
        "aliases:",
    ]
    if not aliases:
        pass
    else:
        for ths in sorted(aliases):
            lines.append(f"  {ths}: # 同花顺")
            for em in aliases[ths]:
                lines.append(f"    - {em} # 东财")
    return "\n".join(lines) + "\n"


def generate_industry_aliases_draft(*, min_score: float = 0.72) -> Path:
    ensure_layout()
    ths_names = fetch_ths_industry_names()
    em_board = fetch_em_board_names()
    jbxx_names = load_jbxx_industry_names()
    em_names = sorted(set(em_board) | set(jbxx_names))

    aliases, meta = build_alias_draft(ths_names, em_names, min_score=min_score)

    meta["sources"] = {
        "ths": len(ths_names),
        "em_board": len(em_board),
        "jbxx_cache": len(jbxx_names),
    }

    out_path = package_industry_aliases_path()
    existing_raw = {}
    if out_path.is_file():
        from quant.scoring.industry_aliases import _load_yaml

        existing_raw = _load_yaml(out_path)
    merged: dict[str, list[str]] = {}
    for block in (existing_raw.get("aliases") or {}, aliases):
        if not isinstance(block, dict):
            continue
        for ths, ems in block.items():
            key = str(ths).strip()
            if not key:
                continue
            bucket = merged.setdefault(key, [])
            if isinstance(ems, list):
                for em in ems:
                    name = str(em).strip()
                    if name and name not in bucket:
                        bucket.append(name)
    for key in merged:
        merged[key].sort()
    aliases = merged
    meta["alias_entries"] = sum(len(v) for v in aliases.values())
    meta["alias_groups"] = len(aliases)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_yaml_dump_aliases(aliases, meta), encoding="utf-8")

    meta_path = quant_cache_file(_META_NAME)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    reload_industry_aliases_cache()
    return out_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    path = generate_industry_aliases_draft()
    meta = json.loads(quant_cache_file(_META_NAME).read_text(encoding="utf-8"))
    print(f"行业映射已写入: {path}")
    print(
        f"同花顺 {meta['ths_count']} | 东财输入 {meta['em_input_count']} | "
        f"映射 {meta['alias_entries']} | 未匹配 {len(meta.get('unmapped_em') or [])}"
    )
    unmapped = meta.get("unmapped_em") or []
    if unmapped:
        print("未匹配（需人工确认）:", "、".join(unmapped[:20]))


if __name__ == "__main__":
    main()
