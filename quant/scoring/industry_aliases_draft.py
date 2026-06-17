"""自动生成东财 ↔ 同花顺行业映射，写回包内配置文件。

数据源（直连 API，全量）：
  - 东财：app.utils.dfcf_util.hy / industry_board_fetch.fetch_em_industry_board
  - 同花顺：app.utils.ths_util.hyylb_all / ths_industry_names

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

from app.utils.dfcf_util import hy as fetch_em_hy
from app.utils.ths_util import hyylb_all, ths_industry_names
from quant.scoring.industry_alias_rules import (
    MANUAL_HINTS,
    apply_alias_corrections,
    recompute_em_only,
    substring_match_allowed,
)
from quant.scoring.industry_aliases import package_industry_aliases_path, reload_industry_aliases_cache
from quant.store.paths import ensure_layout, quant_cache_file
from quant.timeutil import cn_datetime_str

logger = logging.getLogger(__name__)

_META_NAME = "industry_aliases_draft_meta.json"
_ROMAN_SUFFIX = re.compile(r"[ⅡⅢⅣIV]+$")
_STRIP_SUFFIXES = ("行业", "板块")


def normalize_industry_name(name: str) -> str:
    s = str(name).strip()
    s = _ROMAN_SUFFIX.sub("", s)
    for suffix in _STRIP_SUFFIXES:
        if s.endswith(suffix) and len(s) > len(suffix) + 1:
            s = s[: -len(suffix)]
    return s.strip()


def fetch_ths_industry_names(*, retries: int = 3) -> list[str]:
    """同花顺行业「板块」名全量（一览表优先，名称表兜底）。"""
    last_err: Exception | None = None
    for i in range(max(1, retries)):
        try:
            summary = hyylb_all()
            names = sorted(
                {str(r.get("板块", "")).strip() for r in summary if str(r.get("板块", "")).strip()}
            )
            if names:
                return names
        except Exception as exc:
            last_err = exc
            logger.warning("拉取同花顺行业一览 retry=%d err=%s", i + 1, exc)
            time.sleep(2)
    for i in range(max(1, retries)):
        try:
            rows = ths_industry_names()
            names = sorted({str(r.get("name", "")).strip() for r in rows if str(r.get("name", "")).strip()})
            if names:
                return names
        except Exception as exc:
            last_err = exc
            logger.warning("拉取同花顺行业名称 retry=%d err=%s", i + 1, exc)
            time.sleep(2)
    raise RuntimeError("无法拉取同花顺行业列表") from last_err


def fetch_em_board_names(*, retries: int = 3) -> list[str]:
    last_err: Exception | None = None
    for i in range(max(1, retries)):
        try:
            rows = fetch_em_hy()
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
            logger.warning("拉取东财行业板块 retry=%d err=%s", i + 1, exc)
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
        return em, "exact", 1.0

    if em in MANUAL_HINTS:
        hint = MANUAL_HINTS[em]
        if hint in ths_set:
            return hint, "manual_hint", 0.99

    norm = normalize_industry_name(em)
    if norm in ths_set:
        return norm, "normalize", 0.95

    substring_hits: list[str] = []
    for ths in ths_names:
        ths_norm = normalize_industry_name(ths)
        if len(ths) < 2:
            continue
        if not (ths in em or em in ths or ths_norm in norm or norm in ths_norm):
            continue
        if not substring_match_allowed(em, ths):
            continue
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
    """构建完整映射：每个同花顺行业为键（含同名、空映射）；另附 em_only。"""
    ths_set = set(ths_names)
    ths_to_em: dict[str, list[str]] = {ths: [] for ths in ths_names}
    mapping_detail: list[dict[str, Any]] = []
    em_only: list[str] = []
    exact_matches: list[str] = []

    for em in sorted(set(em_names)):
        ths, method, score = propose_ths_match(em, ths_names, ths_set)
        if ths is None and em in MANUAL_HINTS:
            hint = MANUAL_HINTS[em]
            if hint in ths_set and em != hint:
                ths, method, score = hint, "manual_hint", 0.99

        if ths and not substring_match_allowed(em, ths) and method not in ("manual_hint", "exact"):
            ths, method, score = None, "blocked", 0.0

        if method == "exact":
            exact_matches.append(em)
            bucket = ths_to_em.setdefault(em, [])
            if em not in bucket:
                bucket.append(em)
            mapping_detail.append({"em": em, "ths": em, "method": method, "score": 1.0})
            continue

        if ths is None or score < min_score:
            em_only.append(em)
            mapping_detail.append({"em": em, "ths": None, "method": method, "score": round(score, 3)})
            continue

        ths_to_em.setdefault(ths, [])
        if em not in ths_to_em[ths]:
            ths_to_em[ths].append(em)
        mapping_detail.append({"em": em, "ths": ths, "method": method, "score": round(score, 3)})

    for ths in ths_to_em:
        ths_to_em[ths].sort()

    ths_only = sorted(t for t in ths_names if not ths_to_em.get(t) and t not in set(em_names))

    meta = {
        "generated_at": cn_datetime_str(),
        "ths_count": len(ths_names),
        "em_input_count": len(set(em_names)),
        "alias_groups": len(ths_to_em),
        "alias_entries": sum(len(v) for v in ths_to_em.values()),
        "exact_matches": sorted(exact_matches),
        "em_only": sorted(em_only),
        "ths_only": ths_only,
        "unmapped_em": sorted(em_only),
        "mappings": mapping_detail,
    }
    return ths_to_em, meta


def _merge_alias_blocks(*blocks: dict[str, Any]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for block in blocks:
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
    return merged


def _yaml_quote(s: str) -> str:
    if not s:
        return '""'
    if any(c in s for c in ":{}[]#&*!|>'\"%@`"):
        return json.dumps(s, ensure_ascii=False)
    return s


def _yaml_dump_full(
    aliases: dict[str, list[str]],
    meta: dict[str, Any],
    catalog: dict[str, Any],
    em_only: list[str],
) -> str:
    lines = [
        "# 行业名称映射（仅行业域；概念不做映射，概念与行业互不交叉）",
        "#",
        "# 编写约定：",
        "#   catalog.ths / catalog.em — 两侧 API 全量行业清单",
        "#   aliases 键 → 同花顺「板块」名（评分 canonical）",
        "#   aliases 值 → 东财 jbxx「行业」/ 东财板块名（含同名、异名；空列表表示暂无东财对应）",
        "#   em_only — 东财侧暂未匹配到同花顺的条目",
        "#",
        f"# generated_at: {meta.get('generated_at', '')}",
        f"# ths={meta.get('ths_count', 0)} em_input={meta.get('em_input_count', 0)} "
        f"aliases={meta.get('alias_entries', 0)} em_only={len(em_only)} ths_only={len(meta.get('ths_only') or [])}",
        "# 刷新：python -m quant industry_aliases_draft",
        "catalog:",
        f"  generated_at: {meta.get('generated_at', '')}",
        "  ths:",
    ]
    for row in catalog.get("ths") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        code = str(row.get("code", "")).strip()
        if not name:
            continue
        if code:
            lines.append(f"    - name: {_yaml_quote(name)}")
            lines.append(f"      code: {_yaml_quote(code)}")
        else:
            lines.append(f"    - name: {_yaml_quote(name)}")
    lines.append("  em:")
    for row in catalog.get("em") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        code = str(row.get("code", "")).strip()
        if not name:
            continue
        if code:
            lines.append(f"    - name: {_yaml_quote(name)}")
            lines.append(f"      code: {_yaml_quote(code)}")
        else:
            lines.append(f"    - name: {_yaml_quote(name)}")
    lines.append("em_only:")
    if em_only:
        for name in em_only:
            lines.append(f"  - {_yaml_quote(name)}")
    else:
        lines.append("  []")
    lines.append("aliases:")
    for ths in sorted(aliases):
        lines.append(f"  {_yaml_quote(ths)}: # 同花顺")
        items = aliases[ths]
        if not items:
            lines.append("    []")
            continue
        for em in items:
            tag = "同名" if em == ths else "东财"
            lines.append(f"    - {_yaml_quote(em)} # {tag}")
    return "\n".join(lines) + "\n"


def _build_catalog(ths_summary: list[dict], ths_name_rows: list[dict], em_rows: list[dict]) -> dict[str, Any]:
    code_by_name: dict[str, str] = {}
    for row in ths_name_rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        code = str(row.get("code", "")).strip()
        if name and code:
            code_by_name[name] = code

    ths_catalog: list[dict[str, str]] = []
    seen_ths: set[str] = set()
    for row in ths_summary:
        name = str(row.get("板块", "")).strip()
        if not name or name in seen_ths:
            continue
        seen_ths.add(name)
        ths_catalog.append({"name": name, "code": code_by_name.get(name, "")})
    for row in ths_name_rows:
        name = str(row.get("name", "")).strip()
        if not name or name in seen_ths:
            continue
        seen_ths.add(name)
        ths_catalog.append({"name": name, "code": str(row.get("code", "")).strip()})
    ths_catalog.sort(key=lambda x: x["name"])

    em_catalog: list[dict[str, str]] = []
    seen_em: set[str] = set()
    for row in em_rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("板块名称", "")).strip()
        code = str(row.get("板块代码", "")).strip()
        if not name or name in seen_em:
            continue
        seen_em.add(name)
        em_catalog.append({"name": name, "code": code})
    em_catalog.sort(key=lambda x: x["name"])

    return {"ths": ths_catalog, "em": em_catalog}


def generate_industry_aliases_draft(*, min_score: float = 0.72) -> Path:
    ensure_layout()
    ths_summary = hyylb_all()
    ths_name_rows = ths_industry_names()
    em_rows = fetch_em_hy()

    ths_names = fetch_ths_industry_names()
    em_board = [str(r.get("板块名称", "")).strip() for r in em_rows if str(r.get("板块名称", "")).strip()]
    jbxx_names = load_jbxx_industry_names()
    em_names = sorted(set(em_board) | set(jbxx_names))

    aliases, meta = build_alias_draft(ths_names, em_names, min_score=min_score)
    catalog = _build_catalog(ths_summary, ths_name_rows, em_rows)

    meta["sources"] = {
        "ths_summary": len(ths_summary),
        "ths_names": len(ths_name_rows),
        "em_board": len(em_board),
        "jbxx_cache": len(jbxx_names),
    }

    out_path = package_industry_aliases_path()
    existing_raw = {}
    if out_path.is_file():
        from quant.scoring.industry_aliases import _load_yaml

        existing_raw = _load_yaml(out_path)

    merged = _merge_alias_blocks(existing_raw.get("aliases") or {}, aliases)
    for ths in ths_names:
        merged.setdefault(ths, [])
    apply_alias_corrections(merged)

    em_catalog_names = [str(r.get("name", "")).strip() for r in catalog.get("em") or []]
    em_only = recompute_em_only(merged, em_catalog_names)

    meta["alias_entries"] = sum(len(v) for v in merged.values())
    meta["alias_groups"] = len(merged)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        _yaml_dump_full(merged, meta, catalog, em_only),
        encoding="utf-8",
    )

    meta_path = quant_cache_file(_META_NAME)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    reload_industry_aliases_cache()
    return out_path


def repair_industry_aliases_file() -> Path:
    """仅对现有 industry_aliases.yml 应用人工规则，不重拉 API。"""
    from quant.scoring.industry_aliases import _load_yaml

    out_path = package_industry_aliases_path()
    raw = _load_yaml(out_path)
    aliases = raw.get("aliases") or {}
    if not isinstance(aliases, dict):
        aliases = {}
    merged = {str(k): list(v) if isinstance(v, list) else [] for k, v in aliases.items()}
    apply_alias_corrections(merged)
    catalog = raw.get("catalog") or {}
    em_catalog_names = [str(r.get("name", "")).strip() for r in catalog.get("em") or [] if isinstance(r, dict)]
    em_only = recompute_em_only(merged, em_catalog_names)
    meta = {
        "generated_at": cn_datetime_str(),
        "ths_count": len(catalog.get("ths") or []),
        "em_input_count": len(em_catalog_names),
        "alias_entries": sum(len(v) for v in merged.values()),
        "alias_groups": len(merged),
        "repaired": True,
    }
    out_path.write_text(_yaml_dump_full(merged, meta, catalog, em_only), encoding="utf-8")
    reload_industry_aliases_cache()
    return out_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    path = generate_industry_aliases_draft()
    meta = json.loads(quant_cache_file(_META_NAME).read_text(encoding="utf-8"))
    print(f"行业映射已写入: {path}")
    print(
        f"同花顺 {meta['ths_count']} | 东财输入 {meta['em_input_count']} | "
        f"映射项 {meta['alias_entries']} | em_only {len(meta.get('em_only') or [])} | "
        f"ths_only {len(meta.get('ths_only') or [])}"
    )
    em_only = meta.get("em_only") or []
    if em_only:
        print("东财未匹配（需人工确认）:", "、".join(em_only[:25]))


if __name__ == "__main__":
    main()
