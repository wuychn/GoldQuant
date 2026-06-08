"""候选股统一标的池过滤：60/00/30 前缀 + 可选剔除 ST。"""

from __future__ import annotations

from app.utils.common_util import extract_stock_code, filter_symbol_pool_rows, is_allowed_symbol_pool_code
from quant.config import load_gates_config


def _exclude_st(cfg: dict | None = None) -> bool:
    pool = (cfg or load_gates_config()).get("symbol_pool") or {}
    return bool(pool.get("exclude_st", True))


def is_st_name(name: str) -> bool:
    text = str(name or "").strip()
    if not text:
        return False
    upper = text.upper()
    return upper.startswith("*") or "ST" in upper


def apply_symbol_pool_filter(
    rows: list[dict] | None,
    *,
    exclude_st: bool | None = None,
) -> list[dict]:
    """保留沪主/深主/创业板，统一 ``股票代码`` 字段，可选剔除 ST。"""
    skip_st = _exclude_st() if exclude_st is None else exclude_st
    out: list[dict] = []
    for row in filter_symbol_pool_rows(rows):
        name = str(row.get("股票名称") or row.get("名称") or "")
        if skip_st and is_st_name(name):
            continue
        out.append(row)
    return out


def normalize_stock_row(row: dict, *, source: str) -> dict:
    """规范代码字段并写入 ``候选来源``。"""
    code = extract_stock_code(row)
    if not code or not is_allowed_symbol_pool_code(code):
        return {}
    item = dict(row)
    item["股票代码"] = code
    item["代码"] = code
    name = item.get("股票名称") or item.get("名称")
    if name and not item.get("股票名称"):
        item["股票名称"] = name
    item["候选来源"] = source
    return item
