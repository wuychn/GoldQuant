"""个股所属概念日缓存：优先读盘，缺失再问财；同代码当日仅调接口一次。

文件路径：~/.quant/daily/{YYYY-MM-DD}/cache/stock_concepts.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from quant.store.paths import daily_cache, today_str
from quant.timeutil import cn_datetime_str

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "stock_concepts.json"

_store_lock = threading.Lock()
_store_by_date: dict[str, DailyConceptCache] = {}


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


class DailyConceptCache:
    """单个自然日的问财概念文件缓存（进程内单例按 date 复用）。"""

    def __init__(self, trade_date: str, *, path: Path | None = None) -> None:
        self.trade_date = trade_date
        self._path = path or daily_cache(_CACHE_FILENAME, trade_date)
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None
        self._async_locks: dict[str, asyncio.Lock] = {}
        self._async_locks_guard = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, Any]:
        if self._data is not None:
            return self._data
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and str(raw.get("date", "")).strip() == self.trade_date:
                    stocks = raw.get("stocks")
                    self._data = {
                        "date": self.trade_date,
                        "stocks": stocks if isinstance(stocks, dict) else {},
                    }
                    return self._data
            except (json.JSONDecodeError, OSError):
                logger.warning("读取个股概念日缓存失败 path=%s", self._path, exc_info=True)
        self._data = {"date": self.trade_date, "stocks": {}}
        return self._data

    def lookup(self, code: str) -> tuple[bool, list[str] | None]:
        """返回 (是否命中文件, 概念列表)。命中且概念为 None 表示当日已问过财但无结果。"""
        key = str(code).strip()
        if not key:
            return False, None
        stocks = self._load()["stocks"]
        if key not in stocks:
            return False, None
        entry = stocks[key]
        if not isinstance(entry, dict):
            return False, None
        return True, _parse_concepts(entry.get("所属概念"))

    def put(
        self,
        code: str,
        *,
        name: str | None,
        concepts: list[str] | None,
        source: str = "问财",
    ) -> None:
        key = str(code).strip()
        if not key:
            return
        with self._lock:
            data = self._load()
            data["stocks"][key] = {
                "股票名称": str(name or "").strip(),
                "所属概念": concepts,
                "概念来源": source,
                "fetched_at": cn_datetime_str(),
            }
            _write_json_atomic(self._path, data)
            self._data = data

    def async_lock_for(self, code: str) -> asyncio.Lock:
        key = str(code).strip()
        with self._async_locks_guard:
            if key not in self._async_locks:
                self._async_locks[key] = asyncio.Lock()
            return self._async_locks[key]


def get_daily_concept_cache(trade_date: str | None = None) -> DailyConceptCache:
    d = trade_date or today_str()
    with _store_lock:
        store = _store_by_date.get(d)
        if store is None:
            store = DailyConceptCache(d)
            _store_by_date[d] = store
        return store


def _collect_optional_holding_symbols() -> list[tuple[str, str | None]]:
    from quant.store.state import get_holdings, get_optional

    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for row in get_optional() + get_holdings():
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
    """预取自选股 + 持仓股问财概念，返回本次新调用问财的只数。"""
    from app.services.stock_enrich import fetch_stock_concepts_wcxg

    symbols = _collect_optional_holding_symbols()
    if not symbols:
        logger.info("[concept-cache] 预取跳过：自选/持仓为空")
        return 0

    file_cache = get_daily_concept_cache(trade_date)
    mem_cache: dict[str, list[str] | None] = {}
    fetched = 0
    for code, name in symbols:
        hit, _ = file_cache.lookup(code)
        if hit:
            continue
        await fetch_stock_concepts_wcxg(
            code,
            name,
            cache=mem_cache,
            file_cache=file_cache,
        )
        fetched += 1
    logger.info(
        "[concept-cache] 预取完成 date=%s total=%d fetched=%d path=%s",
        file_cache.trade_date,
        len(symbols),
        fetched,
        file_cache.path,
    )
    return fetched
