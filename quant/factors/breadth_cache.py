"""板块成份股广度：批量 AKShare 拉取 + 按日磁盘缓存。

缓存路径：~/.quant/cache/sector_breadth/{date}/{section}/{name}.json
回测优先读缓存 / payload；仅当日生产可打 AKShare 实时成份。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from quant.config import load_r2_config
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY
from quant.store.paths import quant_home
from quant.timeutil import cn_date_str

logger = logging.getLogger(__name__)

_CHG_COLS = ("涨跌幅", "涨跌幅(%)", "涨跌", "change_pct")
_LIMIT_UP_PCT = 9.5


@dataclass
class BreadthSnapshot:
    breadth_pct: float
    up_count: int
    down_count: int
    flat_count: int
    member_count: int
    median_chg: float | None = None
    limit_up_count: int = 0
    source: str = "unknown"

    def apply_to_row(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["上涨家数"] = self.up_count
        out["下跌家数"] = self.down_count
        out["breadth_pct"] = round(self.breadth_pct, 2)
        out["breadth_member_count"] = self.member_count
        out["breadth_median_chg"] = self.median_chg
        out["breadth_limit_up_count"] = self.limit_up_count
        out["breadth_source"] = self.source
        return out


def _safe_name(name: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]', "_", str(name).strip())
    return text[:80] or "unknown"


def cache_dir(date_str: str, section: str) -> Path:
    sec = "industry" if section == BOARD_INDUSTRY else "concept"
    p = quant_home() / "cache" / "sector_breadth" / date_str / sec
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_path(date_str: str, section: str, name: str) -> Path:
    h = hashlib.md5(name.encode("utf-8")).hexdigest()[:10]
    return cache_dir(date_str, section) / f"{_safe_name(name)}_{h}.json"


def _breadth_cfg() -> dict:
    return (load_r2_config().get("sector") or {}).get("breadth_cache") or {}


def _snapshot_from_dict(raw: dict) -> BreadthSnapshot | None:
    try:
        return BreadthSnapshot(
            breadth_pct=float(raw["breadth_pct"]),
            up_count=int(raw["up_count"]),
            down_count=int(raw["down_count"]),
            flat_count=int(raw.get("flat_count") or 0),
            member_count=int(raw["member_count"]),
            median_chg=float(raw["median_chg"]) if raw.get("median_chg") is not None else None,
            limit_up_count=int(raw.get("limit_up_count") or 0),
            source=str(raw.get("source") or "cache"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def load_cached(date_str: str, section: str, name: str) -> BreadthSnapshot | None:
    path = cache_path(date_str, section, name)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    snap = _snapshot_from_dict(raw)
    if snap:
        snap.source = "cache"
    return snap


def save_cached(date_str: str, section: str, name: str, snap: BreadthSnapshot) -> None:
    path = cache_path(date_str, section, name)
    payload = asdict(snap)
    payload["sector"] = name
    payload["section"] = section
    payload["date"] = date_str
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _f(row: dict, *keys: str) -> float | None:
    for k in keys:
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def breadth_from_payload_row(row: dict) -> BreadthSnapshot | None:
    up = _f(row, "上涨家数")
    down = _f(row, "下跌家数")
    if up is not None and down is not None and (up + down) > 0:
        total = int(up + down)
        flat = max(0, int(_f(row, "公司家数") or total) - total)
        member = total + flat
        return BreadthSnapshot(
            breadth_pct=max(0.0, min(100.0, up / total * 100.0)),
            up_count=int(up),
            down_count=int(down),
            flat_count=flat,
            member_count=member,
            source="payload",
        )
    if row.get("breadth_pct") is not None:
        try:
            return BreadthSnapshot(
                breadth_pct=float(row["breadth_pct"]),
                up_count=int(row.get("上涨家数") or 0),
                down_count=int(row.get("下跌家数") or 0),
                flat_count=int(row.get("flat_count") or 0),
                member_count=int(row.get("breadth_member_count") or 0),
                median_chg=_f(row, "breadth_median_chg"),
                limit_up_count=int(row.get("breadth_limit_up_count") or 0),
                source=str(row.get("breadth_source") or "payload"),
            )
        except (TypeError, ValueError):
            pass
    return None


def _compute_from_changes(changes: list[float]) -> BreadthSnapshot:
    if not changes:
        return BreadthSnapshot(50.0, 0, 0, 0, 0, source="akshare")
    up = sum(1 for c in changes if c > 0)
    down = sum(1 for c in changes if c < 0)
    flat = len(changes) - up - down
    sorted_c = sorted(changes)
    mid = sorted_c[len(sorted_c) // 2]
    limit_up = sum(1 for c in changes if c >= _LIMIT_UP_PCT)
    return BreadthSnapshot(
        breadth_pct=up / len(changes) * 100.0,
        up_count=up,
        down_count=down,
        flat_count=flat,
        member_count=len(changes),
        median_chg=round(mid, 3),
        limit_up_count=limit_up,
        source="akshare",
    )


def fetch_live_breadth(name: str, section: str) -> BreadthSnapshot | None:
    try:
        import akshare as ak
    except ImportError:
        return None
    name = str(name).strip()
    if not name:
        return None
    try:
        if section == BOARD_INDUSTRY:
            df = ak.stock_board_industry_cons_em(symbol=name)
        else:
            df = ak.stock_board_concept_cons_em(symbol=name)
    except Exception as exc:
        logger.debug("AKShare breadth fail %s/%s: %s", section, name, exc)
        return None
    if df is None or getattr(df, "empty", True):
        return None
    col = next((c for c in _CHG_COLS if c in df.columns), None)
    if not col:
        return None
    try:
        changes = [float(x) for x in df[col].tolist() if x == x]
    except (TypeError, ValueError):
        return None
    return _compute_from_changes(changes)


def get_breadth(
    date_str: str,
    name: str,
    section: str,
    row: dict | None = None,
    *,
    allow_live_ak: bool | None = None,
) -> BreadthSnapshot | None:
    """解析顺序：磁盘缓存 → payload →（仅当日）AKShare 实时。"""
    cached = load_cached(date_str, section, name)
    if cached:
        return cached

    if row:
        from_payload = breadth_from_payload_row(row)
        if from_payload:
            save_cached(date_str, section, name, from_payload)
            return from_payload

    cfg = _breadth_cfg()
    live_ok = bool(cfg.get("allow_live_ak", True)) if allow_live_ak is None else allow_live_ak
    today = cn_date_str()
    if live_ok and date_str == today:
        live = fetch_live_breadth(name, section)
        if live:
            save_cached(date_str, section, name, live)
            return live
    return None


def enrich_row_breadth(
    row: dict[str, Any],
    *,
    name: str,
    section: str,
    date_str: str | None = None,
    allow_live_ak: bool | None = None,
) -> dict[str, Any]:
    from quant.factors.sector import breadth_from_row

    if breadth_from_row(row) is not None:
        return row
    ds = date_str or cn_date_str()
    snap = get_breadth(ds, name, section, row, allow_live_ak=allow_live_ak)
    if not snap:
        return row
    return snap.apply_to_row(row)


def prefetch_batch(
    items: list[tuple[str, str, dict]],
    *,
    date_str: str,
    allow_live_ak: bool | None = None,
    max_workers: int | None = None,
) -> dict[str, int]:
    """批量预取广度；items = [(name, section, row), ...]。"""
    cfg = _breadth_cfg()
    workers = int(max_workers or cfg.get("max_workers") or 4)
    delay = float(cfg.get("request_delay_sec") or 0.15)
    stats = {"cached": 0, "payload": 0, "akshare": 0, "miss": 0}

    todo: list[tuple[str, str, dict]] = []
    for name, section, row in items:
        if load_cached(date_str, section, name):
            stats["cached"] += 1
            continue
        if row and breadth_from_payload_row(row):
            snap = breadth_from_payload_row(row)
            if snap:
                save_cached(date_str, section, name, snap)
                stats["payload"] += 1
            continue
        todo.append((name, section, row))

    today = cn_date_str()
    live_ok = bool(cfg.get("allow_live_ak", True)) if allow_live_ak is None else allow_live_ak
    if not todo:
        return stats

    def _one(item: tuple[str, str, dict]) -> str:
        n, sec, _r = item
        time.sleep(delay)
        if date_str != today or not live_ok:
            return "miss"
        snap = fetch_live_breadth(n, sec)
        if snap:
            save_cached(date_str, sec, n, snap)
            return "akshare"
        return "miss"

    if date_str == today and live_ok:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_one, it): it for it in todo}
            for fut in as_completed(futs):
                try:
                    tag = fut.result()
                except Exception:
                    tag = "miss"
                stats[tag] = stats.get(tag, 0) + 1
    else:
        stats["miss"] += len(todo)
    return stats


def list_cached_dates() -> list[str]:
    root = quant_home() / "cache" / "sector_breadth"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())
