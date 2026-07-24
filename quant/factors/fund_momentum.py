"""板块资金动量：AKShare 5日/10日排行 + 按日缓存。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY
from quant.store.paths import quant_home
from quant.timeutil import cn_date_str

logger = logging.getLogger(__name__)

_INDICATORS = ("今日", "5日", "10日")


def _cache_path(date_str: str, indicator: str, sector_type: str) -> Path:
    safe = sector_type.replace("/", "_")
    p = quant_home() / "cache" / "sector_fund_flow" / date_str
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{safe}_{indicator}.json"


def _sector_type(section: str) -> str:
    return "行业资金流" if section == BOARD_INDUSTRY else "概念资金流"


def _load_table(date_str: str, indicator: str, section: str) -> dict[str, dict[str, Any]]:
    path = _cache_path(date_str, indicator, _sector_type(section))
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    rows = raw.get("rows") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("名称") or row.get("行业") or row.get("板块") or "").strip()
        if name:
            out[name] = row
    return out


def _fetch_rank(indicator: str, sector_type: str) -> list[dict]:
    try:
        import akshare as ak
    except ImportError:
        return []
    try:
        df = ak.stock_sector_fund_flow_rank(indicator=indicator, sector_type=sector_type)
    except Exception as exc:
        logger.debug("fund rank fail %s %s: %s", indicator, sector_type, exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    return df.to_dict(orient="records")


def save_fund_flow_table(
    date_str: str,
    indicator: str,
    sector_type: str,
    rows: list[dict],
    *,
    source: str = "akshare",
) -> None:
    path = _cache_path(date_str, indicator, sector_type)
    path.write_text(
        json.dumps(
            {"date": date_str, "indicator": indicator, "sector_type": sector_type, "rows": rows, "source": source},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def prefetch_fund_flow(date_str: str | None = None, *, indicators: tuple[str, ...] = _INDICATORS) -> dict[str, int]:
    """拉取概念+行业多周期资金流排行并缓存（生产环境当日调用）。"""
    ds = date_str or cn_date_str()
    counts: dict[str, int] = {}
    for sector_type in ("概念资金流", "行业资金流"):
        for ind in indicators:
            rows = _fetch_rank(ind, sector_type)
            save_fund_flow_table(ds, ind, sector_type, rows, source="akshare")
            counts[f"{sector_type}_{ind}"] = len(rows)
    return counts


def fund_momentum_score(name: str, section: str, *, date_str: str | None = None) -> float:
    """0～100：5日/10日净流入排名与增幅综合。"""
    ds = date_str or cn_date_str()
    today = _load_table(ds, "今日", section).get(name) or {}
    d5 = _load_table(ds, "5日", section).get(name) or {}
    d10 = _load_table(ds, "10日", section).get(name) or {}

    def _net(row: dict) -> float:
        for k in ("净额", "净流入", "主力净流入-净额", "主力净流入净额"):
            v = row.get(k)
            if v is None or v == "":
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return 0.0

    n0, n5, n10 = _net(today), _net(d5), _net(d10)
    accel = n0 - (n5 / 5 if n5 else 0)
    score = 50.0
    if n5 > 0:
        score += min(25.0, n5 ** 0.5 * 3)
    if n10 > 0:
        score += min(10.0, n10 ** 0.5)
    if accel > 0:
        score += min(15.0, accel * 0.5)
    elif accel < -5:
        score -= min(20.0, abs(accel) * 0.3)
    return max(0.0, min(100.0, score))


def enrich_fund_momentum(row: dict, *, name: str, section: str, date_str: str | None = None) -> dict:
    score = fund_momentum_score(name, section, date_str=date_str)
    out = dict(row)
    out["fund_momentum_score"] = round(score, 2)
    return out
