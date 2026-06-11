"""个股 enrich：盘口 / 历史 / 技术指标 / 资金流 / 问财所属概念。"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings
from app.utils.common_util import get_n_workdays_ago, list_to_dict_v2
from app.utils.dfcf_util import hist, pk
from app.utils.quant_archive import (
    daily_hist_fetch_start_date,
    load_computed_metrics_zh,
    load_merge_write_daily_bars,
)
from app.utils.quant_market_enrich import pre_auction_minute_zh
from app.utils.ths_util import ggzjl, wcxg
from quant.progress_log import log_progress_count

logger = logging.getLogger(__name__)

_concept_cache: dict[str, list[str] | None] = {}

CONCEPT_SOURCE_WENCAI = "问财"
CONCEPT_SOURCE_FALLBACK = "来源自带"


def _log_error(context: str) -> None:
    logger.exception("stock_enrich [%s]", context)


def _sync_call_or_none(context: str, fn) -> object | None:
    try:
        return fn()
    except Exception:
        _log_error(context)
        return None


def _row_date_yyyymmdd(row: dict, *, date_key: str = "日期") -> str | None:
    v = row.get(date_key)
    if v is None:
        return None
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y%m%d")
        except Exception:
            pass
    s = str(v).strip().replace("-", "").replace("/", "")[:8]
    if len(s) >= 8 and s[:8].isdigit():
        return s[:8]
    return None


def _yyyymmdd_to_iso(s: str) -> str | None:
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


def _rows_last_n_trade_days(rows: list, *, n: int, date_key: str = "日期") -> list:
    if not isinstance(rows, list) or not rows or n <= 0:
        return []
    dated: list[tuple[str, dict]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = _row_date_yyyymmdd(r, date_key=date_key)
        if d:
            dated.append((d, r))
    if not dated:
        return list(rows[-n:]) if len(rows) >= n else list(rows)
    dated.sort(key=lambda x: x[0])
    anchor = dated[-1][0]
    iso = _yyyymmdd_to_iso(anchor)
    if not iso:
        return [r for _, r in dated[-n:]]
    oldest = get_n_workdays_ago(iso, n - 1)
    if oldest is None:
        return [r for _, r in dated[-n:]]
    return [r for d, r in dated if oldest <= d <= anchor]


def _load_hist(settings: Settings, symbol: str) -> list:
    if settings.QUANT_ARCHIVE_ENABLED:
        start_d = daily_hist_fetch_start_date(settings, symbol)
        hist_api = _sync_call_or_none(
            f"历史行情 | ak.stock_zh_a_hist symbol={symbol!r}",
            lambda: hist(symbol, period="daily", start_date=start_d),
        )
        if not isinstance(hist_api, list):
            hist_api = []
        hist_ = load_merge_write_daily_bars(settings, symbol, hist_api)
    else:
        def _hist_no_archive() -> object:
            start = get_n_workdays_ago(None, 60)
            if start:
                return hist(symbol, start_date=start)
            return hist(symbol)

        hist_ = _sync_call_or_none(
            f"历史行情 | ak.stock_zh_a_hist symbol={symbol!r}",
            _hist_no_archive,
        )
    if not hist_:
        hist_ = []
    return _rows_last_n_trade_days(hist_, n=30)


def _parse_existing_concepts(item: dict) -> list[str] | None:
    """解析行内已有概念；``无`` / 空视为缺失。"""
    raw = item.get("所属概念") if item.get("所属概念") is not None else item.get("概念")
    if isinstance(raw, list):
        out = [str(x).strip() for x in raw if str(x).strip()]
        out = [x for x in out if x not in ("无", "-", "—")]
        return out or None
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text in ("无", "-", "—"):
            return None
        parts = [x.strip() for x in text.replace(";", "、").replace(",", "、").split("、") if x.strip()]
        return parts or None
    return None


def _infer_fallback_concept_source(item: dict) -> str:
    src = str(item.get("概念来源") or "").strip()
    if src:
        return src
    source = str(item.get("候选来源") or "").strip()
    if source == "人气榜" or "人气" in source:
        return "人气榜"
    return CONCEPT_SOURCE_FALLBACK


async def attach_stock_concepts_from_wencai(
    row: dict[str, Any],
    *,
    cache: dict[str, list[str] | None] | None = None,
    file_cache=None,
) -> dict[str, Any]:
    """优先日缓存文件，缺失再问财（同日同代码仅调一次）；失败或空时回退行内已有概念。"""
    item = dict(row)
    symbol = str(item.get("股票代码", "")).strip()
    if not symbol:
        return item

    fallback = _parse_existing_concepts(item)
    stock_name = item.get("股票名称")
    concepts = await fetch_stock_concepts_wcxg(
        symbol,
        stock_name if isinstance(stock_name, str) else None,
        cache=cache,
        file_cache=file_cache,
    )
    if concepts:
        item["所属概念"] = concepts
        item["概念来源"] = CONCEPT_SOURCE_WENCAI
    elif fallback:
        item["所属概念"] = fallback
        item["概念来源"] = _infer_fallback_concept_source(item)
    return item


async def attach_concepts_to_rows(
    rows: list[dict],
    *,
    cache: dict[str, list[str] | None] | None = None,
    file_cache=None,
    progress_scope: str | None = None,
    progress_label: str = "问财",
) -> list[dict]:
    from app.services.stock_concept_cache import get_daily_concept_cache

    store = cache if cache is not None else _concept_cache
    day_cache = file_cache if file_cache is not None else get_daily_concept_cache()
    out: list[dict] = []
    total = len(rows)
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        out.append(
            await attach_stock_concepts_from_wencai(row, cache=store, file_cache=day_cache)
        )
        if progress_scope and total and (i == 0 or i + 1 == total or (i + 1) % 5 == 0):
            code = str(row.get("股票代码", "")).strip()
            log_progress_count(progress_scope, progress_label, i + 1, total, detail=code)
    return out


async def fetch_stock_concepts_wcxg(
    symbol: str,
    name: str | None = None,
    *,
    cache: dict[str, list[str] | None] | None = None,
    file_cache=None,
) -> list[str] | None:
    from app.services.stock_concept_cache import get_daily_concept_cache

    key = str(symbol).strip()
    if not key:
        return None
    store = cache if cache is not None else _concept_cache
    if key in store:
        return store[key]

    day_cache = file_cache if file_cache is not None else get_daily_concept_cache()
    hit, cached = day_cache.lookup(key)
    if hit:
        store[key] = cached
        return cached

    async with day_cache.async_lock_for(key):
        if key in store:
            return store[key]
        hit, cached = day_cache.lookup(key)
        if hit:
            store[key] = cached
            return cached

        question = key
        if name:
            question = f"{question} {str(name).strip()}"
        try:
            concepts = await wcxg(question)
            result = concepts if concepts else None
        except Exception:
            _log_error(f"问财所属概念 symbol={symbol!r}")
            result = None
        day_cache.put(key, name=name, concepts=result)
        store[key] = result
        return result


async def _ggzjl(symbol: str) -> dict | None:
    try:
        r = await ggzjl(symbol)
        flash_ = r["flash"]
        v_ = list_to_dict_v2(flash_, "name", "sr")
        v_["大单流出"] = f"{v_['大单流出']} 万元"
        v_["中单流出"] = f"{v_['中单流出']} 万元"
        v_["小单流出"] = f"{v_['小单流出']} 万元"
        v_["小单流入"] = f"{v_['小单流入']} 万元"
        v_["中单流入"] = f"{v_['中单流入']} 万元"
        v_["大单流入"] = f"{v_['大单流入']} 万元"
        v_["总流入"] = f"{r['title']['zlr']} 万元"
        v_["总流出"] = f"{r['title']['zlc']} 万元"
        v_["净额"] = f"{r['title']['je']} 万元"
        return v_
    except Exception:
        _log_error(f"个股资金流 symbol={symbol!r}")
        return None


async def enrich_stock_row(
    settings: Settings,
    row: dict[str, Any],
    *,
    include_pre_snapshot: bool = False,
    concept_cache: dict[str, list[str] | None] | None = None,
    concept_file_cache=None,
    skip_wencai: bool = False,
) -> dict[str, Any]:
    item = dict(row)
    symbol = str(item.get("股票代码", "")).strip()
    if not symbol:
        return item

    if not skip_wencai:
        item = await attach_stock_concepts_from_wencai(
            item,
            cache=concept_cache,
            file_cache=concept_file_cache,
        )

    from app.services.stock_jbxx_cache import fetch_jbxx_cached

    jbxx_ = fetch_jbxx_cached(symbol)
    if isinstance(jbxx_, dict):
        for k in ("总股本", "流通股", "总市值", "流通市值", "上市时间"):
            if k in jbxx_:
                item[k] = jbxx_[k]

    pk_raw = _sync_call_or_none("盘口", lambda: pk(symbol))
    item["盘口"] = pk_raw if isinstance(pk_raw, dict) else {}

    item["历史行情"] = _load_hist(settings, symbol)

    if settings.QUANT_ARCHIVE_ENABLED:
        tzh = load_computed_metrics_zh(settings, symbol)
        if tzh:
            item["技术指标"] = tzh

    if include_pre_snapshot:
        pm = pre_auction_minute_zh("分钟行情 | ak.stock_zh_a_hist_pre_min_em", symbol)
        item["分钟行情"] = pm if isinstance(pm, list) else []

    zj_raw = await _ggzjl(symbol)
    if zj_raw:
        item["个股资金流"] = zj_raw

    return item


async def enrich_stock_rows(
    settings: Settings,
    rows: list[dict],
    *,
    include_pre_snapshot: bool = False,
    skip_wencai: bool = False,
    concept_cache: dict[str, list[str] | None] | None = None,
    concept_file_cache=None,
    progress_scope: str | None = None,
    progress_label: str = "enrich",
) -> list[dict]:
    from app.services.stock_concept_cache import get_daily_concept_cache

    cache = concept_cache if concept_cache is not None else _concept_cache
    day_cache = concept_file_cache if concept_file_cache is not None else get_daily_concept_cache()
    out: list[dict] = []
    total = len(rows)
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        out.append(
            await enrich_stock_row(
                settings,
                row,
                include_pre_snapshot=include_pre_snapshot,
                concept_cache=cache,
                concept_file_cache=day_cache,
                skip_wencai=skip_wencai,
            )
        )
        if progress_scope and total and (i == 0 or i + 1 == total or (i + 1) % 5 == 0):
            code = str(row.get("股票代码", "")).strip()
            log_progress_count(progress_scope, progress_label, i + 1, total, detail=code)
    return out
