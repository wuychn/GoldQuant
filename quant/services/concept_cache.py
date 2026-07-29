"""个股所属概念 / 同花顺 F10 概念粘合度周缓存：优先读盘，缺失或过期再调接口。

文件路径：~/.quant/cache/stock_concepts.json（单条有效期 7 天）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

from quant.store.paths import quant_cache_file
from common.timeutil import cn_datetime_str, cn_now, parse_cn_datetime_str

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "stock_concepts.json"
_TTL_DAYS = 7

_store_lock = threading.Lock()
_store: StockConceptCache | None = None


def _write_json_atomic(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _parse_concepts(raw: object) -> list[str] | None:
    if raw is None:
        return None
    if isinstance(raw, list):
        out = [str(x).strip() for x in raw if str(x).strip()]
        return out or None
    return None


def _parse_fit_ranks(raw: object) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        concept = str(item.get("concept") or "").strip()
        rank = item.get("rank")
        if concept and isinstance(rank, int) and rank > 0:
            out.append({"rank": rank, "concept": concept})
    if not out:
        return None
    out.sort(key=lambda x: x["rank"])
    return out


class StockConceptCache:
    """问财概念 + 同花顺 F10 概念粘合度周缓存（进程内单例）。"""

    def __init__(
        self,
        trade_date: str | None = None,
        *,
        path: Path | None = None,
        ttl_days: int = _TTL_DAYS,
    ) -> None:
        del trade_date  # 兼容旧 DailyConceptCache(trade_date) 调用
        self._path = path or quant_cache_file(_CACHE_FILENAME)
        self._ttl = timedelta(days=max(1, int(ttl_days)))
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None
        self._async_locks: dict[str, asyncio.Lock] = {}
        self._async_locks_guard = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def ttl_days(self) -> int:
        return self._ttl.days

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    stocks = raw.get("stocks")
                    self._data = {
                        "stocks": stocks if isinstance(stocks, dict) else {},
                    }
                    return self._data
            except (json.JSONDecodeError, OSError):
                logger.warning("读取个股概念缓存失败 path=%s", self._path, exc_info=True)
        self._data = {"stocks": {}}
        return self._data

    def _is_fresh(self, fetched_at: str) -> bool:
        dt = parse_cn_datetime_str(fetched_at)
        if dt is None:
            return False
        return cn_now() - dt < self._ttl

    def _fresh_entry(self, code: str) -> dict[str, Any] | None:
        key = str(code).strip()
        if not key:
            return None
        entry = self._load()["stocks"].get(key)
        if not isinstance(entry, dict):
            return None
        fetched_at = str(entry.get("fetched_at", "")).strip()
        if not fetched_at or not self._is_fresh(fetched_at):
            return None
        return entry

    def lookup(self, code: str) -> tuple[bool, list[str] | None]:
        """返回 (是否命中有效缓存, 概念列表)。命中且概念为 None 表示已问过财但无结果。"""
        entry = self._fresh_entry(code)
        if entry is None:
            return False, None
        return True, _parse_concepts(entry.get("所属概念"))

    def lookup_entry(self, code: str) -> tuple[bool, dict[str, Any] | None]:
        entry = self._fresh_entry(code)
        if entry is None:
            return False, None
        return True, entry

    def lookup_fit(self, code: str) -> tuple[bool, list[dict[str, Any]] | None, list[str] | None]:
        """返回 (是否命中有效缓存, 概念粘合度列表, 所属概念列表)。"""
        entry = self._fresh_entry(code)
        if entry is None:
            return False, None, None
        return (
            True,
            _parse_fit_ranks(entry.get("概念粘合度")),
            _parse_concepts(entry.get("所属概念")),
        )

    def put(
        self,
        code: str,
        *,
        name: str | None,
        concepts: list[str] | None,
        source: str = "问财",
        fit_ranks: list[dict[str, Any]] | None = None,
    ) -> None:
        key = str(code).strip()
        if not key:
            return
        with self._lock:
            data = self._load()
            prev = data["stocks"].get(key)
            entry: dict[str, Any] = {
                "股票名称": str(name or "").strip(),
                "所属概念": concepts,
                "概念来源": source,
                "fetched_at": cn_datetime_str(),
            }
            if fit_ranks:
                entry["概念粘合度"] = fit_ranks
            elif isinstance(prev, dict):
                prev_fit = _parse_fit_ranks(prev.get("概念粘合度"))
                if prev_fit:
                    entry["概念粘合度"] = prev_fit
            data["stocks"][key] = entry
            _write_json_atomic(self._path, data)
            self._data = data

    def async_lock_for(self, code: str) -> asyncio.Lock:
        key = str(code).strip()
        with self._async_locks_guard:
            if key not in self._async_locks:
                self._async_locks[key] = asyncio.Lock()
            return self._async_locks[key]


# 兼容旧名
DailyConceptCache = StockConceptCache


def get_stock_concept_cache() -> StockConceptCache:
    global _store
    with _store_lock:
        if _store is None:
            _store = StockConceptCache()
        return _store


def get_daily_concept_cache(trade_date: str | None = None) -> StockConceptCache:
    """兼容旧调用（``trade_date`` 已忽略，统一走周缓存）。"""
    del trade_date
    return get_stock_concept_cache()


def _collect_optional_holding_symbols() -> list[tuple[str, str | None]]:
    from quant.store.state import get_holdings

    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for row in get_holdings():
        if not isinstance(row, dict):
            continue
        code = str(row.get("股票代码", "")).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        name = row.get("股票名称")
        out.append((code, name if isinstance(name, str) else None))
    return out


async def prefetch_optional_holding_concepts(
    *,
    trade_date: str | None = None,
) -> int:
    """预取自选股 + 持仓股概念/粘合度，返回本次新调接口的只数。"""
    del trade_date
    from quant.services.enrich import attach_stock_concepts

    symbols = _collect_optional_holding_symbols()
    if not symbols:
        logger.info("[concept-cache] 预取跳过：自选/持仓为空")
        return 0

    file_cache = get_stock_concept_cache()
    mem_cache: dict[str, list[str] | None] = {}
    fit_cache: dict[str, list[dict] | None] = {}
    fetched = 0
    for code, name in symbols:
        hit, fit, _ = file_cache.lookup_fit(code)
        if hit and fit:
            continue
        hit, concepts = file_cache.lookup(code)
        if hit and concepts:
            continue
        await attach_stock_concepts(
            {"股票代码": code, "股票名称": name or ""},
            cache=mem_cache,
            fit_cache=fit_cache,
            file_cache=file_cache,
        )
        fetched += 1
    logger.info(
        "[concept-cache] 预取完成 ttl=%dd total=%d fetched=%d path=%s",
        file_cache.ttl_days,
        len(symbols),
        fetched,
        file_cache.path,
    )
    return fetched
