"""个股基本信息周缓存：优先读盘，缺失或过期再调东财接口。

文件路径：~/.quant/cache/stock_jbxx.json（单条有效期 7 天）
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from quant.store.paths import quant_cache_file
from quant.timeutil import cn_datetime_str, cn_now, parse_cn_datetime_str
from app.utils.error_log import log_caught_error

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "stock_jbxx.json"
_TTL_DAYS = 7

_store_lock = threading.Lock()
_store: StockJbxxCache | None = None


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


def _parse_ts(s: str) -> datetime | None:
    return parse_cn_datetime_str(s)


class StockJbxxCache:
    def __init__(self, *, path: Path | None = None, ttl_days: int = _TTL_DAYS) -> None:
        self._path = path or quant_cache_file(_CACHE_FILENAME)
        self._ttl = timedelta(days=max(1, int(ttl_days)))
        self._lock = threading.Lock()
        self._data: dict[str, Any] | None = None
        self._code_locks: dict[str, threading.Lock] = {}
        self._code_locks_guard = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

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
                logger.warning("读取个股基本信息缓存失败 path=%s", self._path, exc_info=True)
        self._data = {"stocks": {}}
        return self._data

    def _is_fresh(self, fetched_at: str) -> bool:
        dt = _parse_ts(fetched_at)
        if dt is None:
            return False
        return cn_now() - dt < self._ttl

    def lookup(self, code: str) -> tuple[bool, dict[str, Any] | None]:
        """返回 (是否命中有效缓存, 基本信息 dict)。"""
        key = str(code).strip()
        if not key:
            return False, None
        entry = self._load()["stocks"].get(key)
        if not isinstance(entry, dict):
            return False, None
        fetched_at = str(entry.get("fetched_at", "")).strip()
        if not fetched_at or not self._is_fresh(fetched_at):
            return False, None
        data = entry.get("data")
        return True, data if isinstance(data, dict) else None

    def put(self, code: str, data: dict[str, Any] | None) -> None:
        if not isinstance(data, dict) or not data:
            return
        key = str(code).strip()
        if not key:
            return
        with self._lock:
            body = self._load()
            body["stocks"][key] = {
                "data": data,
                "fetched_at": cn_datetime_str(),
            }
            _write_json_atomic(self._path, body)
            self._data = body

    def lock_for(self, code: str) -> threading.Lock:
        key = str(code).strip()
        with self._code_locks_guard:
            if key not in self._code_locks:
                self._code_locks[key] = threading.Lock()
            return self._code_locks[key]


def get_stock_jbxx_cache() -> StockJbxxCache:
    global _store
    with _store_lock:
        if _store is None:
            _store = StockJbxxCache()
        return _store


def fetch_jbxx_cached(symbol: str, *, file_cache: StockJbxxCache | None = None) -> dict[str, Any] | None:
    """带周缓存的基本信息；缓存未命中时调东财 ``jbxx``。"""
    key = str(symbol).strip()
    if not key:
        return None

    store = file_cache if file_cache is not None else get_stock_jbxx_cache()
    hit, data = store.lookup(key)
    if hit:
        return data

    with store.lock_for(key):
        hit, data = store.lookup(key)
        if hit:
            return data
        try:
            from app.utils.dfcf_util import jbxx

            raw = jbxx(key)
            data = raw if isinstance(raw, dict) and raw else None
        except Exception as e:
            log_caught_error(logger, f"stock_jbxx_cache 拉取基本信息 symbol={key!r}", e)
            return None
        if data:
            store.put(key, data)
        return data


def prefetch_optional_holding_jbxx() -> int:
    """预取自选股 + 持仓股基本信息，返回本次新调用接口的只数。"""
    from app.services.stock_concept_cache import _collect_optional_holding_symbols

    symbols = _collect_optional_holding_symbols()
    if not symbols:
        logger.info("[jbxx-cache] 预取跳过：自选/持仓为空")
        return 0

    store = get_stock_jbxx_cache()
    fetched = 0
    for code, _name in symbols:
        hit, _ = store.lookup(code)
        if hit:
            continue
        fetch_jbxx_cached(code, file_cache=store)
        fetched += 1
    logger.info(
        "[jbxx-cache] 预取完成 total=%d fetched=%d path=%s",
        len(symbols),
        fetched,
        store.path,
    )
    return fetched
