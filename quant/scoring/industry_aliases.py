"""行业映射：东财 jbxx「行业」→ 同花顺 hyylb「板块」。

映射文件 quant/config/industry_aliases.yml 约定：
  - catalog — 两侧 API 全量行业清单（维护用，评分不读）
  - em_only — 东财暂未匹配同花顺的条目（维护用）
  - aliases — 键为同花顺板块名，值为东财别名列表（评分用）
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_PACKAGE_ALIASES = Path(__file__).resolve().parent.parent / "config" / "industry_aliases.yml"


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def load_industry_alias_maps() -> tuple[dict[str, list[str]], dict[str, str]]:
    """返回 (ths_canonical→em_aliases, em_alias→ths_canonical)。"""
    raw = _load_yaml(_PACKAGE_ALIASES)
    aliases_block = raw.get("aliases") or {}
    ths_to_em: dict[str, list[str]] = {}
    em_to_ths: dict[str, str] = {}

    if not isinstance(aliases_block, dict):
        return ths_to_em, em_to_ths

    for ths_name, aliases in aliases_block.items():
        canonical = str(ths_name).strip()
        if not canonical:
            continue
        norm_aliases: list[str] = []
        if isinstance(aliases, list):
            for alias in aliases:
                em_name = str(alias).strip()
                if not em_name or em_name == canonical:
                    continue
                norm_aliases.append(em_name)
                em_to_ths[em_name] = canonical
        ths_to_em[canonical] = norm_aliases

    return ths_to_em, em_to_ths


def reload_industry_aliases_cache() -> None:
    load_industry_alias_maps.cache_clear()


def expand_industries(industries: set[str]) -> set[str]:
    """东财行业名展开为同花顺榜单名（保留原名以便恰好一致时也能命中）。"""
    if not industries:
        return set()
    _, em_to_ths = load_industry_alias_maps()
    out = set(industries)
    for name in industries:
        mapped = em_to_ths.get(name)
        if mapped:
            out.add(mapped)
    return out


def map_industry_to_ths(name: str) -> str:
    """单条东财行业 → 同花顺板块名；无映射时返回原名。"""
    n = str(name).strip()
    if not n:
        return ""
    _, em_to_ths = load_industry_alias_maps()
    return em_to_ths.get(n, n)


def package_industry_aliases_path() -> Path:
    return _PACKAGE_ALIASES
