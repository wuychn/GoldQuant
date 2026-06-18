"""测试阶段（``GOLDQUANT_QUANT_TEST_PHASE``）下统一截断 API 数据侧列表长度。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

QUANT_TEST_LIST_LIMIT = 3


def trim_for_test_phase(obj: Any, *, limit: int = QUANT_TEST_LIST_LIMIT) -> Any:
    """递归将各层 list 截断为 ``limit`` 条；``rows`` / ``row_count`` 同步更新。"""
    if obj is None:
        return None
    if isinstance(obj, BaseModel):
        return trim_for_test_phase(obj.model_dump(), limit=limit)
    if isinstance(obj, list):
        return [trim_for_test_phase(item, limit=limit) for item in obj[:limit]]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if key == "rows" and isinstance(value, list):
                trimmed = [
                    trim_for_test_phase(item, limit=limit) for item in value[:limit]
                ]
                out[key] = trimmed
                if "row_count" in obj:
                    out["row_count"] = len(trimmed)
                continue
            out[key] = trim_for_test_phase(value, limit=limit)
        if "rows" in out and isinstance(out["rows"], list):
            if "row_count" not in out and "row_count" in obj:
                out["row_count"] = len(out["rows"])
        return out
    return obj


def maybe_trim_for_test_phase(obj: Any) -> Any:
    """若开启测试阶段则截断，否则原样返回。"""
    from app.core.config import get_settings

    settings = get_settings()
    limit = settings.quant_test_list_limit()
    if limit is None:
        return obj
    return trim_for_test_phase(obj, limit=limit)


def truncate_list_for_test_phase(
    rows: list[Any] | None,
    settings: Any | None = None,
) -> list[Any]:
    """测试阶段将列表截为 ``quant_test_list_limit`` 条（供初筛/enrich 前使用）。"""
    if not rows:
        return []
    from app.core.config import get_settings

    settings = settings or get_settings()
    limit = settings.quant_test_list_limit()
    if limit is None:
        return list(rows)
    return list(rows[:limit])
