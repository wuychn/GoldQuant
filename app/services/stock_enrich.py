"""个股 enrich：盘口 / 历史 / 技术指标 / 资金流 / 问财所属概念。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.core.config import Settings
from app.utils.common_util import get_n_workdays_ago, list_to_dict_v2
from app.utils.dfcf_util import hist, pk
from app.utils.quant_archive import (
    daily_hist_fetch_start_date,
    load_computed_metrics_zh,
    load_merge_write_daily_bars,
)
from app.utils.quant_market_enrich import stock_intraday_minute_zh
from app.utils.ths_util import ggzjl, wcxg
from app.utils.ths_funds_fetch import ThsFundsFetchError
from app.utils.error_log import log_caught_error
from quant.progress_log import log_progress, log_progress_count

logger = logging.getLogger(__name__)

_concept_cache: dict[str, list[str] | None] = {}
_fit_rank_cache: dict[str, list[dict[str, Any]] | None] = {}

CONCEPT_SOURCE_WENCAI = "问财"
CONCEPT_SOURCE_THS_FIT = "同花顺F10粘合度"
CONCEPT_SOURCE_FALLBACK = "来源自带"

ConceptFetchSource = Literal["memory", "file", "api"]


@dataclass(frozen=True)
class ConceptFetchResult:
    concepts: list[str] | None
    source: ConceptFetchSource | None = None


@dataclass(frozen=True)
class ConceptFitFetchResult:
    fit_ranks: list[dict[str, Any]] | None
    source: ConceptFetchSource | None = None


def concept_fetch_progress_label(source: ConceptFetchSource | None) -> str:
    """进度日志用：区分缓存命中与问财接口。"""
    if source in ("memory", "file"):
        return "概念·缓存"
    if source == "api":
        return "概念·问财"
    return "概念"


def concept_fit_fetch_progress_label(source: ConceptFetchSource | None) -> str:
    if source in ("memory", "file"):
        return "粘合度·缓存"
    if source == "api":
        return "粘合度·同花顺F10"
    return "粘合度"


def _log_error(context: str, exc: Exception | None = None) -> None:
    log_caught_error(logger, f"stock_enrich [{context}]", exc)


def _sync_call_or_none(context: str, fn) -> object | None:
    try:
        return fn()
    except Exception as e:
        _log_error(context, e)
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


def _hist_max_bars(settings: Settings, hist_max_bars: int | None) -> int:
    if hist_max_bars is not None and hist_max_bars > 0:
        return hist_max_bars
    return max(20, int(settings.QUANT_HIST_SCORING_MAX_BARS))


def _load_hist(settings: Settings, symbol: str, *, hist_max_bars: int | None = None) -> list:
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
    return _rows_last_n_trade_days(hist_, n=_hist_max_bars(settings, hist_max_bars))


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


async def fetch_stock_concept_fit_ths(
    symbol: str,
    name: str | None = None,
    *,
    cache: dict[str, list[dict[str, Any]] | None] | None = None,
    file_cache=None,
    api_allowed: bool = True,
) -> ConceptFitFetchResult:
    """同花顺 F10 概念粘合度：内存 → 周文件缓存 → HTTP。"""
    from app.services.stock_concept_cache import get_stock_concept_cache
    from app.utils.ths_concept_fit_rank import get_concept_fit_rank_list

    key = str(symbol).strip()
    if not key:
        return ConceptFitFetchResult(None, None)
    store = cache if cache is not None else _fit_rank_cache
    if key in store:
        return ConceptFitFetchResult(store[key], "memory")

    file_cache = file_cache if file_cache is not None else get_stock_concept_cache()
    hit, fit, _ = file_cache.lookup_fit(key)
    if hit and fit is not None:
        store[key] = fit
        return ConceptFitFetchResult(fit, "file")
    if hit and fit is None and not api_allowed:
        store[key] = None
        return ConceptFitFetchResult(None, "file")

    async with file_cache.async_lock_for(key):
        if key in store:
            return ConceptFitFetchResult(store[key], "memory")
        hit, fit, _ = file_cache.lookup_fit(key)
        if hit and fit is not None:
            store[key] = fit
            return ConceptFitFetchResult(fit, "file")
        if hit and fit is None and not api_allowed:
            store[key] = None
            return ConceptFitFetchResult(None, "file")

        if not api_allowed:
            store[key] = None
            return ConceptFitFetchResult(None, None)

        try:
            rows = await asyncio.to_thread(get_concept_fit_rank_list, key)
            result = rows if rows else None
        except Exception:
            _log_error(f"同花顺F10概念粘合度 symbol={symbol!r}")
            result = None

        if result:
            concepts = [str(r["concept"]).strip() for r in result if str(r.get("concept", "")).strip()]
            file_cache.put(
                key,
                name=name,
                concepts=concepts or None,
                source=CONCEPT_SOURCE_THS_FIT,
                fit_ranks=result,
            )
            store[key] = result
            return ConceptFitFetchResult(result, "api")

        store[key] = None
        return ConceptFitFetchResult(None, "api")


async def attach_stock_concepts(
    row: dict[str, Any],
    *,
    cache: dict[str, list[str] | None] | None = None,
    fit_cache: dict[str, list[dict[str, Any]] | None] | None = None,
    file_cache=None,
    api_allowed: bool = True,
) -> tuple[dict[str, Any], ConceptFetchSource | None]:
    """优先同花顺 F10 概念粘合度，缺失再问财或行内已有概念。"""
    item = dict(row)
    symbol = str(item.get("股票代码", "")).strip()
    if not symbol:
        return item, None

    stock_name = item.get("股票名称")
    name = stock_name if isinstance(stock_name, str) else None
    fit = await fetch_stock_concept_fit_ths(
        symbol,
        name,
        cache=fit_cache,
        file_cache=file_cache,
        api_allowed=api_allowed,
    )
    if fit.fit_ranks:
        item["所属概念"] = [str(r["concept"]).strip() for r in fit.fit_ranks]
        item["概念粘合度"] = fit.fit_ranks
        item["概念来源"] = CONCEPT_SOURCE_THS_FIT
        return item, fit.source

    return await attach_stock_concepts_from_wencai(
        item,
        cache=cache,
        file_cache=file_cache,
        api_allowed=api_allowed,
    )


async def attach_stock_concepts_from_wencai(
    row: dict[str, Any],
    *,
    cache: dict[str, list[str] | None] | None = None,
    file_cache=None,
    api_allowed: bool = True,
) -> tuple[dict[str, Any], ConceptFetchSource | None]:
    """优先周缓存文件，缺失再问财（同代码缓存期内仅调一次）；失败或空时回退行内已有概念。"""
    item = dict(row)
    symbol = str(item.get("股票代码", "")).strip()
    if not symbol:
        return item, None

    fallback = _parse_existing_concepts(item)
    stock_name = item.get("股票名称")
    fetched = await fetch_stock_concepts_wcxg(
        symbol,
        stock_name if isinstance(stock_name, str) else None,
        cache=cache,
        file_cache=file_cache,
        api_allowed=api_allowed,
    )
    if fetched.concepts:
        item["所属概念"] = fetched.concepts
        item["概念来源"] = CONCEPT_SOURCE_WENCAI
    elif fallback:
        item["所属概念"] = fallback
        item["概念来源"] = _infer_fallback_concept_source(item)
        return item, None
    return item, fetched.source


async def attach_concepts_to_rows(
    rows: list[dict],
    *,
    cache: dict[str, list[str] | None] | None = None,
    file_cache=None,
    progress_scope: str | None = None,
) -> list[dict]:
    from app.services.stock_concept_cache import get_stock_concept_cache

    store = cache if cache is not None else _concept_cache
    concept_file_cache = file_cache if file_cache is not None else get_stock_concept_cache()
    out: list[dict] = []
    total = len(rows)
    cache_hits = 0
    api_calls = 0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        item, source = await attach_stock_concepts(
            row, cache=store, file_cache=concept_file_cache
        )
        out.append(item)
        if source in ("memory", "file"):
            cache_hits += 1
        elif source == "api":
            api_calls += 1
        if progress_scope and total and (i == 0 or i + 1 == total or (i + 1) % 5 == 0):
            code = str(row.get("股票代码", "")).strip()
            log_progress_count(
                progress_scope,
                concept_fetch_progress_label(source),
                i + 1,
                total,
                detail=code,
            )
    if progress_scope and total:
        log_progress(
            progress_scope,
            "补概念汇总",
            detail=f"共 {total} 只，缓存 {cache_hits}，问财 {api_calls}",
        )
    return out


async def fetch_stock_concepts_wcxg(
    symbol: str,
    name: str | None = None,
    *,
    cache: dict[str, list[str] | None] | None = None,
    file_cache=None,
    api_allowed: bool = True,
) -> ConceptFetchResult:
    """问财概念：内存 → 周文件缓存；命中且概念非空则返回，否则在 ``api_allowed`` 时调问财。"""
    from app.services.stock_concept_cache import get_stock_concept_cache

    key = str(symbol).strip()
    if not key:
        return ConceptFetchResult(None, None)
    store = cache if cache is not None else _concept_cache
    if key in store:
        return ConceptFetchResult(store[key], "memory")

    file_cache = file_cache if file_cache is not None else get_stock_concept_cache()
    hit, cached = file_cache.lookup(key)
    if hit and (cached is not None or not api_allowed):
        store[key] = cached
        return ConceptFetchResult(cached, "file")

    async with file_cache.async_lock_for(key):
        if key in store:
            return ConceptFetchResult(store[key], "memory")
        hit, cached = file_cache.lookup(key)
        if hit and (cached is not None or not api_allowed):
            store[key] = cached
            return ConceptFetchResult(cached, "file")

        if not api_allowed:
            store[key] = None
            return ConceptFetchResult(None, None)

        question = key
        if name:
            question = f"{question} {str(name).strip()}"
        try:
            concepts = await wcxg(question)
            result = concepts if concepts else None
        except Exception:
            _log_error(f"问财所属概念 symbol={symbol!r}")
            result = None
        file_cache.put(key, name=name, concepts=result)
        store[key] = result
        return ConceptFetchResult(result, "api")


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
    except ThsFundsFetchError as exc:
        status = f" HTTP {exc.http_status}" if exc.http_status is not None else ""
        log_caught_error(logger, f"stock_enrich [个股资金流 symbol={symbol!r}{status}]", exc)
        return None
    except Exception as exc:
        _log_error(f"个股资金流 symbol={symbol!r}")
        return None


async def _fund_flow_daily(symbol: str, *, days: int = 10) -> list[dict] | None:
    try:
        from app.utils.dfcf_util import zj

        rows = await asyncio.to_thread(zj, symbol)
        if not isinstance(rows, list) or not rows:
            return None
        return rows[-days:]
    except Exception:
        _log_error(f"个股资金流日线 symbol={symbol!r}")
        return None


async def _attach_concepts_cache_only(
    item: dict[str, Any],
    *,
    cache: dict[str, list[str] | None] | None = None,
    file_cache=None,
) -> tuple[dict[str, Any], ConceptFetchSource | None]:
    """仅内存/日文件缓存或行内已有概念，不调外部 API。"""
    return await attach_stock_concepts(
        item,
        cache=cache,
        file_cache=file_cache,
        api_allowed=False,
    )


async def _ensure_stock_industry(
    item: dict[str, Any],
    symbol: str,
    *,
    allow_network: bool,
) -> None:
    """``行业`` 为评分硬依赖：jbxx 全量失败时也尽量从缓存或单独补全。"""
    if str(item.get("行业") or "").strip():
        return
    from app.services.stock_jbxx_cache import fetch_stock_industry

    ind = await asyncio.to_thread(
        fetch_stock_industry,
        symbol,
        allow_network=allow_network,
    )
    if ind:
        item["行业"] = ind


async def enrich_stock_row(
    settings: Settings,
    row: dict[str, Any],
    *,
    include_pre_snapshot: bool = False,
    hist_max_bars: int | None = None,
    concept_cache: dict[str, list[str] | None] | None = None,
    concept_file_cache=None,
    skip_wencai: bool = False,
    skip_jbxx: bool = False,
) -> tuple[dict[str, Any], ConceptFetchSource | None]:
    item = dict(row)
    symbol = str(item.get("股票代码", "")).strip()
    concept_source: ConceptFetchSource | None = None
    if not symbol:
        return item, None

    if skip_wencai:
        item, concept_source = await _attach_concepts_cache_only(
            item,
            cache=concept_cache,
            file_cache=concept_file_cache,
        )
    else:
        item, concept_source = await attach_stock_concepts(
            item,
            cache=concept_cache,
            file_cache=concept_file_cache,
        )

    if not skip_jbxx:
        from app.services.stock_jbxx_cache import fetch_jbxx_cached

        jbxx_ = await asyncio.to_thread(fetch_jbxx_cached, symbol)
        if isinstance(jbxx_, dict):
            for k in ("总股本", "流通股", "总市值", "流通市值", "上市时间", "行业"):
                if k in jbxx_:
                    item[k] = jbxx_[k]

    await _ensure_stock_industry(item, symbol, allow_network=not skip_jbxx)

    io_tasks: list[Any] = [
        asyncio.to_thread(_sync_call_or_none, "盘口", lambda: pk(symbol)),
        asyncio.to_thread(_load_hist, settings, symbol, hist_max_bars=hist_max_bars),
        _ggzjl(symbol),
        _fund_flow_daily(symbol),
    ]
    if include_pre_snapshot:
        io_tasks.append(
            asyncio.to_thread(
                stock_intraday_minute_zh,
                "分钟行情 | ak.stock_zh_a_hist_pre_min_em",
                symbol,
            )
        )
    io_results = await asyncio.gather(*io_tasks)
    pk_raw = io_results[0]
    hist_ = io_results[1]
    zj_raw = io_results[2]
    zj_daily = io_results[3]
    pm = io_results[4] if include_pre_snapshot else None

    item["盘口"] = pk_raw if isinstance(pk_raw, dict) else {}
    item["历史行情"] = hist_ if isinstance(hist_, list) else []

    if settings.QUANT_ARCHIVE_ENABLED:
        tzh = load_computed_metrics_zh(settings, symbol)
        if tzh:
            item["技术指标"] = tzh

    if include_pre_snapshot:
        item["分钟行情"] = pm if isinstance(pm, list) else []

    if zj_raw:
        item["个股资金流"] = zj_raw
    if zj_daily:
        item["个股资金流日线"] = zj_daily

    return item, concept_source


def _resolve_enrich_runtime(
    settings: Settings,
    progress_scope: str | None,
    max_concurrency: int | None,
) -> tuple[int, int | None, int]:
    """返回 (并发, 分批大小或 None, 批间暂停秒)。"""
    if progress_scope == "post_market_evening":
        conc = (
            max_concurrency
            if max_concurrency is not None
            else settings.QUANT_ENRICH_EVENING_CONCURRENCY
        )
        batch_size = settings.QUANT_ENRICH_EVENING_BATCH_SIZE
        pause = settings.QUANT_ENRICH_EVENING_BATCH_PAUSE_SEC
        return max(1, conc), (batch_size if batch_size > 0 else None), max(0, pause)
    conc = max_concurrency if max_concurrency is not None else settings.QUANT_ENRICH_CONCURRENCY
    return max(1, conc), None, 0


async def enrich_stock_rows(
    settings: Settings,
    rows: list[dict],
    *,
    include_pre_snapshot: bool = False,
    hist_max_bars: int | None = None,
    skip_wencai: bool = False,
    skip_jbxx: bool = False,
    concept_cache: dict[str, list[str] | None] | None = None,
    concept_file_cache=None,
    progress_scope: str | None = None,
    progress_label: str = "enrich",
    max_concurrency: int | None = None,
) -> list[dict]:
    from app.services.stock_concept_cache import get_stock_concept_cache

    cache = concept_cache if concept_cache is not None else _concept_cache
    concept_file_cache = concept_file_cache if concept_file_cache is not None else get_stock_concept_cache()
    indexed: list[tuple[int, dict]] = [
        (i, row) for i, row in enumerate(rows) if isinstance(row, dict)
    ]
    total = len(indexed)
    if not indexed:
        return []

    conc, batch_size, batch_pause = _resolve_enrich_runtime(settings, progress_scope, max_concurrency)
    if batch_size and total > batch_size:
        chunks: list[list[tuple[int, dict]]] = [
            indexed[i : i + batch_size] for i in range(0, total, batch_size)
        ]
    else:
        chunks = [indexed]

    sem = asyncio.Semaphore(conc)
    cache_hits = 0
    api_calls = 0
    done = 0
    progress_lock = asyncio.Lock()

    async def _one(idx: int, row: dict) -> tuple[int, dict, ConceptFetchSource | None]:
        nonlocal cache_hits, api_calls, done
        async with sem:
            item, concept_source = await enrich_stock_row(
                settings,
                row,
                include_pre_snapshot=include_pre_snapshot,
                hist_max_bars=hist_max_bars,
                concept_cache=cache,
                concept_file_cache=concept_file_cache,
                skip_wencai=skip_wencai,
                skip_jbxx=skip_jbxx,
            )
        async with progress_lock:
            if concept_source in ("memory", "file"):
                cache_hits += 1
            elif concept_source == "api":
                api_calls += 1
            done += 1
            if progress_scope and total and (
                done == 1 or done == total or done % 5 == 0
            ):
                code = str(row.get("股票代码", "")).strip()
                if concept_source is not None or not skip_wencai:
                    log_progress_count(
                        progress_scope,
                        concept_fetch_progress_label(concept_source),
                        done,
                        total,
                        detail=code,
                    )
                log_progress_count(progress_scope, progress_label, done, total, detail=code)
        return idx, item, concept_source

    all_results: list[tuple[int, dict, ConceptFetchSource | None]] = []
    n_batches = len(chunks)
    for batch_idx, chunk in enumerate(chunks):
        if batch_idx > 0 and batch_pause > 0:
            if progress_scope:
                log_progress(
                    progress_scope,
                    "enrich 批次间等待",
                    detail=f"暂停 {batch_pause}s · 即将第 {batch_idx + 1}/{n_batches} 批",
                )
            await asyncio.sleep(batch_pause)
        if n_batches > 1 and progress_scope:
            log_progress(
                progress_scope,
                "enrich 批次开始",
                detail=f"第 {batch_idx + 1}/{n_batches} 批 · {len(chunk)} 只 · 并发 {conc}",
            )
        batch_results = await asyncio.gather(*(_one(i, row) for i, row in chunk))
        all_results.extend(batch_results)

    all_results.sort(key=lambda x: x[0])
    out = [item for _, item, _ in all_results]
    if progress_scope and total and (cache_hits or api_calls):
        batch_note = f"，{n_batches} 批" if n_batches > 1 else ""
        log_progress(
            progress_scope,
            "补概念汇总",
            detail=f"共 {total} 只，缓存 {cache_hits}，问财 {api_calls}，并发 {conc}{batch_note}",
        )
    return out
