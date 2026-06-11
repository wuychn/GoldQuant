"""openclaw量化数据入口"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from numbers import Integral, Real
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import akshare as ak
from fastapi import APIRouter, BackgroundTasks
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.schemas.response import Response
from app.utils.common_util import (
    get_n_workdays_ago,
    get_val,
    _normalize_quant_datetime_string,
    _should_normalize_datetime_like_string,
    _yyyymmdd_to_iso,
)
from app.utils.dataframe import dataframe_to_records
from app.utils.dfcf_util import ztgc, ztgc_with_date
from app.services.stock_enrich import attach_stock_concepts_from_wencai, enrich_stock_rows
from app.utils.etf52_util import zdfb_52etf
from app.utils.ths_util import stock_fund_flow_concept, hot_stock, zdfb_ths
from quant.pool.candidate_config import PAYLOAD_KEY_PKYD, PAYLOAD_KEY_POPULARITY, PAYLOAD_KEY_ZT
from quant.pool.candidate_sources import build_all_source_candidates
from quant.pool.pkyd_util import (
    build_pkyd_tag_map,
    enrich_list_with_pkyd_tags,
    enrich_zt_stats_with_pkyd,
)
from quant.pool.sources import prefilter_popularity
from quant.pool.symbol_filter import apply_symbol_pool_filter
from quant.progress_log import log_progress, log_progress_count, log_progress_done

logger = logging.getLogger(__name__)

router = APIRouter(tags=["量化入口"])

_INDEX_SERIAL_WHITELIST = (1, 2, 4)
# 自选/持仓落盘：~/.quant/ 下 JSONL（一行一条 JSON 对象）
QUANT_OPTIONAL_FILENAME = "state/optional.jsonl"
QUANT_HOLDING_FILENAME = "state/holding.jsonl"

_SH_TZ = ZoneInfo("Asia/Shanghai")


# ---------------------------------------------------------------------------
# 辅助函数 / 聚合逻辑（路由入口均在文件末尾）
# ---------------------------------------------------------------------------

def _sync_call_or_none(context: str, fn: Callable[[], object]) -> object | None:
    try:
        return fn()
    except Exception:
        _log_api_error(context)
        return None


def _normalize_quant_datetimes(obj: Any) -> Any:
    """将疑似日期时间的字符串规范为 ``yyyy-MM-dd HH:mm:ss`` 或 ``yyyy-MM-dd``。"""
    if isinstance(obj, dict):
        return {k: _normalize_quant_datetimes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_quant_datetimes(v) for v in obj]
    if isinstance(obj, str) and _should_normalize_datetime_like_string(obj):
        return _normalize_quant_datetime_string(obj)
    return obj


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


def _rows_last_n_trade_days(
        rows: list,
        *,
        n: int,
        date_key: str = "日期",
) -> list:
    """锚日为行中最大 ``date_key``；保留 [第 n-1 个交易日, 锚日] 闭区间（含锚日共至多 n 个交易日）。"""
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
    out = [r for d, r in dated if oldest <= d <= anchor]
    return out


def _round_floats_for_api(obj: Any, *, ndigits: int = 2) -> Any:
    """递归将浮点数四舍五入到 ``ndigits`` 位；整数、布尔、字符串等保持原样。"""
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, Integral) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, Real) and not isinstance(obj, bool):
        return round(float(obj), ndigits)
    if isinstance(obj, dict):
        return {k: _round_floats_for_api(v, ndigits=ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats_for_api(v, ndigits=ndigits) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_round_floats_for_api(v, ndigits=ndigits) for v in obj)
    return obj


def _finalize_quant_payload(obj: Any) -> Any:
    """深拷贝 → 日期时间规范化 → 浮点舍入（用于接口统一出参）。"""
    try:
        cloned = copy.deepcopy(obj)
    except Exception:
        cloned = obj
    out = _round_floats_for_api(_normalize_quant_datetimes(cloned))
    if isinstance(out, dict):
        from quant.store.state import merge_payload_holdings

        out = merge_payload_holdings(out)
    return out


def _merge_concept_boards(jzf: list | None, jzj: list | None, jdf: list | None, jzjlc: list | None, *, limit: int = 10) -> dict[str, Any]:
    return {
        "涨幅榜": (jzf or [])[:limit],
        "跌幅榜": (jdf or [])[:limit],
        "资金流入榜": (jzj or [])[:limit],
        "资金流出榜": (jzjlc or [])[:limit],
    }


def _log_api_error(context: str) -> None:
    """记录上游/本地调用失败，带固定上下文便于定位，避免单点异常拖垮整次聚合。"""
    logger.exception("量化数据接口异常 [%s]", context)


async def zjl_(n: int) -> list | None:
    """
    大盘资金流
    经过测试，盘中获取到的可能都是上一个交易日的数据，需要确认盘后能否获取到当天的数据 TODO
    """
    try:
        recs = dataframe_to_records(await run_in_threadpool(ak.stock_market_fund_flow))
        if not recs:
            return []
        if n <= 0:
            return []
        return recs[-n:] if len(recs) >= n else recs
    except Exception:
        _log_api_error("大盘资金流 | ak.stock_market_fund_flow")
        return None


async def _stock_fund_flow_concept_or_none(context: str, sort_key: str, desc=True):
    try:
        return await stock_fund_flow_concept("即时", sort_key, desc)
    except Exception:
        _log_api_error(f"{context} sort_key={sort_key!r}")
        return None


async def _enrich_stock_list(
        settings: SettingsDep,
        fetch_stocks: Callable[..., Awaitable[list]],
        *,
        include_pre_snapshot: bool = False,
) -> list:
    try:
        rows = await fetch_stocks(settings)
    except Exception:
        _log_api_error("_enrich_stock_list fetch")
        return []
    if not rows:
        return []
    try:
        return await enrich_stock_rows(settings, rows, include_pre_snapshot=include_pre_snapshot)
    except Exception:
        _log_api_error("_enrich_stock_list enrich")
        return rows


async def _async_optional_rows(_settings: SettingsDep) -> list:
    from quant.store.state import get_optional

    return await run_in_threadpool(get_optional)


async def _async_holding_rows(_settings: SettingsDep) -> list:
    from quant.store.state import get_holdings

    return await run_in_threadpool(get_holdings)


async def _enrich_optional_and_holding(
    settings: SettingsDep,
    *,
    progress_scope: str | None = None,
) -> tuple[list, list]:
    """自选 + 持仓两行列表，结构与原先两次 ``_enrich_stock_list`` 调用一致。"""
    if progress_scope:
        log_progress(progress_scope, "enrich 自选股")
    zxg = await _enrich_stock_list(settings, _async_optional_rows, include_pre_snapshot=True)
    if progress_scope:
        log_progress(progress_scope, "enrich 持仓股", detail=f"自选 {len(zxg)} 只")
    ccg = await _enrich_stock_list(settings, _async_holding_rows, include_pre_snapshot=True)
    if progress_scope:
        log_progress(progress_scope, "自选/持仓 enrich 完成", detail=f"持仓 {len(ccg)} 只")
    return zxg, ccg


def _quant_data_file(name: str) -> Path:
    return Path.home() / ".quant" / name


def _parse_jsonl_stock_text(text: str) -> list:
    """JSONL：每行一条 JSON 对象；``#`` 开头行为注释；单行若为数组则展开其中对象。"""
    raw: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            for it in obj:
                if isinstance(it, dict):
                    raw.append(it)
        elif isinstance(obj, dict):
            raw.append(obj)
    return _normalize_quant_stock_rows(raw)


def _normalize_quant_stock_rows(raw: list | None) -> list:
    if not raw:
        return []
    out: list = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = item.get("股票代码")
        if code is None or str(code).strip() == "":
            continue
        row = dict(item)
        row["股票代码"] = str(code).strip()
        out.append(row)
    return out


def _zt_height(pool: list[dict[str, Any]] | None):
    """从涨停池中取最大 ``连板数`` 作为市场连板高度（数值）。"""
    if not pool:
        return None
    mx = 0
    for r in pool:
        v = r.get("连板数")
        try:
            if v is not None and v != "":
                mx = max(mx, int(float(v)))
        except (TypeError, ValueError):
            continue
    return mx


def _load_stock_rows_from_quant_file(filename: str) -> list:
    """读取 ``~/.quant/{filename}``，仅支持 JSONL（一行一条 JSON）。"""
    path = _quant_data_file(filename)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        _log_api_error(f"quant read file path={path!s}")
        return []
    return _parse_jsonl_stock_text(text)


def _combine_cls_publish_datetime(pub_date_val: object, pub_time_val: object) -> str:
    """财联社 ``发布日期`` + ``发布时间`` → ``yyyy-MM-dd HH:mm:ss``。"""
    ds = str(pub_date_val or "").strip()
    ts = str(pub_time_val or "").strip()
    day = ""
    if "T" in ds:
        day = ds.split("T")[0].replace("/", "-")[:10]
    elif re.match(r"^\d{4}-\d{2}-\d{2}", ds):
        day = ds[:10]
    if not day or len(day) < 10:
        day = datetime.now(_SH_TZ).strftime("%Y-%m-%d")
    ts = ts.replace("：", ":").strip()
    if not ts:
        return f"{day} 00:00:00"
    parts = [p for p in ts.split(":") if p != ""]
    try:
        if len(parts) >= 3:
            return f"{day} {int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
        if len(parts) == 2:
            return f"{day} {int(parts[0]):02d}:{int(parts[1]):02d}:00"
    except (ValueError, IndexError):
        pass
    return f"{day} {ts}"


async def _dpzs():
    """获取大盘指数（东财）。"""
    try:
        raw = dataframe_to_records(
            await run_in_threadpool(lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"))
        )
        return [
            item
            for item in raw
            if item.get("序号") in _INDEX_SERIAL_WHITELIST
        ]
    except Exception:
        _log_api_error("大盘指数 | ak.stock_zh_index_spot_em")
        return None


async def _zqxy(*, market_phase: str = "intraday"):
    """涨跌分布 / 赚钱效应（多数据源回退）。"""
    try:
        return await zdfb_52etf(market_phase=market_phase)
    except Exception:
        _log_api_error("赚钱效应 | 52etf涨跌分布")

    try:
        return await zdfb_ths()
    except Exception:
        _log_api_error("赚钱效应 | 同花顺涨跌分布")

    # legu 盘前常为上一交易日口径且易失败，暂不接入
    return None


def _apply_row_limit(rows: list | None, limit: int | None) -> list:
    if not isinstance(rows, list):
        return []
    allowed = apply_symbol_pool_filter(rows)
    if limit is None:
        return allowed
    return allowed[:limit]


def _ztgk_rows(
    settings: SettingsDep,
    zt_full: list,
    *,
    more: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    row_limit = settings.quant_bulk_row_limit()
    zt_allowed = apply_symbol_pool_filter(zt_full)
    height = _zt_height(zt_allowed)
    result["今日涨停"] = zt_allowed[:row_limit] if row_limit is not None else zt_allowed
    result["市场高度"] = f"{height}连板"
    if more:
        try:
            zrzt = ztgc_with_date(get_n_workdays_ago(n=1))
            result["昨日涨停"] = _apply_row_limit(zrzt, row_limit)
        except Exception:
            _log_api_error("昨日涨停股池全量 | ztgc_with_date")
    return result


async def _ztgk(settings: SettingsDep, more: bool = False, *, zt_full: list | None = None):
    try:
        pool = zt_full if zt_full is not None else await run_in_threadpool(ztgc)
        return _ztgk_rows(settings, pool if isinstance(pool, list) else [], more=more)
    except Exception:
        _log_api_error("今日涨停股全量 | ztgc")
        return {}


async def _hot(settings: SettingsDep, *, progress_scope: str | None = "during_market"):
    """盘中等人气榜展示：初筛 + 问财概念（不走完整 enrich）。"""
    try:
        n = settings.quant_hot_list_limit()
        raw_hot = await hot_stock(settings, n)
        rows = prefilter_popularity(raw_hot if isinstance(raw_hot, list) else [])
        scope = progress_scope or "during_market"
        log_progress(scope, "人气榜问财补概念", detail=f"共 {len(rows)} 只")
        from app.services.stock_concept_cache import get_daily_concept_cache

        out: list[dict[str, Any]] = []
        mem_cache: dict[str, list[str] | None] = {}
        day_cache = get_daily_concept_cache()
        total = len(rows)
        for i, item in enumerate(rows):
            if not isinstance(item, dict):
                continue
            out.append(
                await attach_stock_concepts_from_wencai(
                    item,
                    cache=mem_cache,
                    file_cache=day_cache,
                )
            )
            if total and (i == 0 or i + 1 == total or (i + 1) % 5 == 0):
                code = str(item.get("股票代码", "")).strip()
                log_progress_count(scope, "问财", i + 1, total, detail=code)
        return out
    except Exception:
        _log_api_error("同花顺人气股 | ths.hot_stock (no enrich)")
        return []


def _concept_payload_stub(gn_bk: dict | None) -> dict[str, Any]:
    return {"概念板块": gn_bk or {}}


# ---------------------------------------------------------------------------
# 路由入口（保持集中于此，便于比对 OpenAPI）
# ---------------------------------------------------------------------------


@router.get(
    "/quant/market/news",
    response_model=Response,
    summary="新闻",
    description="新闻",
)
async def news(settings: SettingsDep) -> Response:
    """
    全球/同花顺/财联社资讯聚合到 ``news`` 列表后，**每次请求**调用
    ``refresh_news_market_summary_sync``：使用全局 LLM（``.env`` 中 ``LLM_API_KEY`` /
    ``LLM_BASE_URL`` / ``LLM_MODEL``，或兼容 ``GOLDQUANT_LLM_*``）生成 **500 字以内** 当日影响摘要，
    成功则 **覆盖** 写入 ``~/.quant/news_market_impact_summary.txt``；
    未配置密钥或 LLM 失败则不覆盖该文件。
    """
    scope = "news"
    log_progress(scope, "开始聚合新闻")
    news: list = []

    # 全球财经资讯
    log_progress(scope, "拉取东方财富全球资讯")
    try:
        dfcf_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_em))
        if dfcf_data:
            for d in dfcf_data:
                if get_val(d, "标题") or get_val(d, "摘要"):
                    row = {
                        "标题": get_val(d, "标题"),
                        "摘要": get_val(d, "摘要"),
                        "来源": "东方财富",
                    }
                    p = get_val(d, "发布时间")
                    if p not in (None, ""):
                        row["发布时间"] = str(p).strip()
                    news.append(row)
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_em")

    # 同花顺财经
    log_progress(scope, "拉取同花顺财经", detail=f"已累计 {len(news)} 条")
    try:
        ths_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_ths))
        if ths_data:
            for d in ths_data:
                if get_val(d, "标题") or get_val(d, "内容"):
                    pt = get_val(d, "发布时间")
                    news.append(
                        {
                            "标题": get_val(d, "标题"),
                            "摘要": get_val(d, "内容"),
                            "发布时间": str(pt).strip() if pt not in (None, "") else "",
                            "来源": "同花顺",
                        }
                    )
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_ths")

    # 财联社电报
    log_progress(scope, "拉取财联社电报", detail=f"已累计 {len(news)} 条")
    try:
        # 财联社404 TODO
        pass
        # cls_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_cls))
        # if cls_data:
        #     for d in cls_data:
        #         if get_val(d, "标题", "") or get_val(d, "摘要", ""):
        #             news.append(
        #                 {
        #                     "标题": get_val(d, "标题"),
        #                     "摘要": get_val(d, "摘要"),
        #                     "发布时间": _combine_cls_publish_datetime(
        #                         get_val(d, "发布日期"),
        #                         get_val(d, "发布时间"),
        #                     ),
        #                     "来源": "财联社",
        #                 }
        #             )
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_cls")

    log_progress_done(scope, "新闻聚合完成", detail=f"共 {len(news)} 条")
    return Response(data=_finalize_quant_payload(news))


@router.get(
    "/quant/market/pre_market",
    response_model=Response,
    summary="盘前",
    description="盘前",
)
async def pre_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    """
    盘前
    """
    scope = "pre_market"
    log_progress(scope, "开始构建盘前 payload")
    log_progress(scope, "拉取大盘指数")
    dpzs_ = await _dpzs()

    log_progress(scope, "拉取赚钱效应")
    zqxy_ = await _zqxy(market_phase="intraday")

    log_progress(scope, "拉取涨停概况")
    ztgk_ = await _ztgk(settings)

    zxg_, ccg_ = await _enrich_optional_and_holding(settings, progress_scope=scope)

    result = {
        "大盘指数": dpzs_,
        "赚钱效应": zqxy_,
        "涨停概况": ztgk_,
        "自选股": zxg_,
        "持仓股": ccg_,
    }

    log_progress_done(scope, "盘前 payload 完成")
    return Response(data=_finalize_quant_payload(result))


@router.get(
    "/quant/market/during_market",
    response_model=Response,
    summary="盘中",
    description="盘中",
)
async def during_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    """
    盘中
    """
    scope = "during_market"
    log_progress(scope, "开始构建盘中 payload")

    log_progress(scope, "拉取大盘指数")
    dpzs = await _dpzs()

    log_progress(scope, "拉取赚钱效应")
    zqxy_ = await _zqxy(market_phase="intraday")

    log_progress(scope, "拉取概念四榜")
    jrzfqsgn = await _stock_fund_flow_concept_or_none(
        "涨幅前十概念 | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
    )

    # 跌幅前十概念
    jrdfqsgn = await _stock_fund_flow_concept_or_none(
        "涨幅前十概念 | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
        False
    )

    # 资金流入前十概念
    jrzjlrqsgn = await _stock_fund_flow_concept_or_none(
        "资金流入前十概念 | ths.stock_fund_flow_concept",
        "净额",
    )

    # 资金流出前十概念
    jrzjlcqsgn = await _stock_fund_flow_concept_or_none(
        "资金流入前十概念 | ths.stock_fund_flow_concept",
        "净额",
        False
    )

    # 合并涨幅和资金流入
    gn_bk = _merge_concept_boards(jrzfqsgn, jrzjlrqsgn, jrdfqsgn, jrzjlcqsgn)

    # 涨停概况
    log_progress(scope, "拉取涨停统计")
    zttj = await _ztgk(settings, True)

    log_progress(scope, "拉取人气榜并问财补概念")
    hot_ = await _hot(settings)

    zxg_, ccg_ = await _enrich_optional_and_holding(settings, progress_scope=scope)

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy_,
        "概念板块": gn_bk,
        "涨停统计": zttj,
        PAYLOAD_KEY_POPULARITY: hot_,
        "自选股": zxg_,
        "持仓股": ccg_,
    }
    log_progress_done(scope, "盘中 payload 完成")
    return Response(data=_finalize_quant_payload(result))


@router.get(
    "/quant/market/post_market_lunch",
    response_model=Response,
    summary="午间复盘",
    description="午间复盘",
)
async def post_market_lunch(settings: SettingsDep) -> Response:
    """
    盘后
    """
    scope = "post_market_lunch"
    log_progress(scope, "开始构建午间 payload")

    log_progress(scope, "拉取大盘指数")
    dpzs = await _dpzs()

    log_progress(scope, "拉取赚钱效应")
    zqxy_ = await _zqxy(market_phase="intraday")

    log_progress(scope, "拉取概念四榜")
    jrzfqsgn = await _stock_fund_flow_concept_or_none(
        "涨幅前十概念 | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
    )

    # 跌幅前十概念
    jrdfqsgn = await _stock_fund_flow_concept_or_none(
        "涨幅前十概念 | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
        False
    )

    # 资金流入前十概念
    jrzjlrqsgn = await _stock_fund_flow_concept_or_none(
        "资金流入前十概念 | ths.stock_fund_flow_concept",
        "净额",
    )

    # 资金流出前十概念
    jrzjlcqsgn = await _stock_fund_flow_concept_or_none(
        "资金流入前十概念 | ths.stock_fund_flow_concept",
        "净额",
        False
    )

    # 合并涨幅和资金流入
    gn_bk = _merge_concept_boards(jrzfqsgn, jrzjlrqsgn, jrdfqsgn, jrzjlcqsgn)

    # 涨停概况
    log_progress(scope, "拉取涨停统计")
    zttj = await _ztgk(settings, True)

    zxg_, ccg_ = await _enrich_optional_and_holding(settings, progress_scope=scope)

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy_,
        "概念板块": gn_bk,
        "涨停统计": zttj,
        "自选股": zxg_,
        "持仓股": ccg_,
    }
    log_progress_done(scope, "午间 payload 完成")
    return Response(data=_finalize_quant_payload(result))


@router.get(
    "/quant/market/post_market_evening",
    response_model=Response,
    summary="晚间复盘",
    description="晚间复盘",
)
async def post_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    """
    盘后
    """
    scope = "post_market_evening"
    log_progress(scope, "开始构建晚间 payload")

    log_progress(scope, "拉取大盘指数")
    dpzs = await _dpzs()

    log_progress(scope, "拉取赚钱效应")
    zqxy_ = await _zqxy(market_phase="closed")

    log_progress(scope, "拉取大盘资金流")
    zjl = await zjl_(3)

    log_progress(scope, "拉取概念四榜")
    jrzfqsgn = await _stock_fund_flow_concept_or_none(
        "涨幅前十概念 | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
    )

    # 跌幅前十概念
    jrdfqsgn = await _stock_fund_flow_concept_or_none(
        "跌幅前十概念 | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
        False
    )

    # 资金流入前十概念
    jrzjlrqsgn = await _stock_fund_flow_concept_or_none(
        "资金流入前十概念 | ths.stock_fund_flow_concept",
        "净额",
    )

    # 资金流出前十概念
    jrzjlcqsgn = await _stock_fund_flow_concept_or_none(
        "资金流出前十概念 | ths.stock_fund_flow_concept",
        "净额",
        False
    )

    # 合并涨幅和资金流入
    gn_bk = _merge_concept_boards(jrzfqsgn, jrzjlrqsgn, jrdfqsgn, jrzjlcqsgn)

    log_progress(scope, "拉取涨停全量")
    zt_full = await run_in_threadpool(ztgc)
    zttj = await _ztgk(settings, True, zt_full=zt_full if isinstance(zt_full, list) else [])

    payload_stub = _concept_payload_stub(gn_bk)
    log_progress(scope, "构建三来源候选（初筛→问财→enrich）")
    sources = await build_all_source_candidates(
        settings,
        payload_stub,
        zt_rows=zt_full if isinstance(zt_full, list) else [],
        include_pre_snapshot=True,
        progress_scope=scope,
    )
    hot_ = sources[PAYLOAD_KEY_POPULARITY]
    zt_candidates = sources[PAYLOAD_KEY_ZT]
    pkyd_list = sources[PAYLOAD_KEY_PKYD]
    tag_map = build_pkyd_tag_map(pkyd_list)
    hot_ = enrich_list_with_pkyd_tags(hot_, tag_map)
    zttj = enrich_zt_stats_with_pkyd(zttj, tag_map)

    zxg_, ccg_ = await _enrich_optional_and_holding(settings, progress_scope=scope)

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy_,
        "大盘资金流": zjl,
        "概念板块": gn_bk,
        "涨停统计": zttj,
        PAYLOAD_KEY_POPULARITY: hot_,
        PAYLOAD_KEY_ZT: zt_candidates,
        PAYLOAD_KEY_PKYD: pkyd_list,
        "自选股": zxg_,
        "持仓股": ccg_,
    }
    log_progress_done(scope, "晚间 payload 完成")
    return Response(data=_finalize_quant_payload(result))
