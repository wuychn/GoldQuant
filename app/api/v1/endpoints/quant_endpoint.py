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
    is_allowed_symbol_pool_code,
    list_to_dict_v2,
    normalize_a_share_code,
    _normalize_quant_datetime_string,
    _should_normalize_datetime_like_string,
    _yyyymmdd_to_iso,
)
from app.utils.dataframe import dataframe_to_records
from app.utils.dfcf_util import pk, ztgc, hist, jbxx, ztgc_with_date, pkyd
from app.utils.etf52_util import zdfb_52etf
from app.utils.quant_archive import (
    load_computed_metrics_zh,
    daily_hist_fetch_start_date,
    load_merge_write_daily_bars,
)
from app.utils.quant_market_enrich import pre_auction_minute_zh
from app.utils.ths_util import stock_fund_flow_concept, hot_stock, zdfb_ths, ggzjl, wcxg
from quant.pool.pkyd_util import (
    build_pkyd_tag_map,
    enrich_list_with_pkyd_tags,
    enrich_zt_stats_with_pkyd,
    merge_pkyd_rows_by_code,
    stock_pkyd_tags,
)

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
    return _round_floats_for_api(_normalize_quant_datetimes(cloned))


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


def _jbxx(symbol):
    try:
        return jbxx(symbol)
    except Exception:
        _log_api_error("股票基本信息 | ak.stock_individual_info_em")
        return None


async def _fetch_stock_concepts_wcxg(
    symbol: str,
    name: str | None = None,
    *,
    cache: dict[str, list[str] | None] | None = None,
) -> list[str] | None:
    """问财查询个股所属概念（可多条）；失败时返回 None，由评分阶段回退人气榜。"""
    key = str(symbol).strip()
    if cache is not None and key in cache:
        return cache[key]
    question = key
    if name:
        question = f"{question} {str(name).strip()}"
    try:
        concepts = await wcxg(question)
        result = concepts if concepts else None
    except Exception:
        _log_api_error(f"个股所属概念 wcxg symbol={symbol!r}")
        result = None
    if cache is not None:
        cache[key] = result
    return result


async def _ggzjl(symbol):
    try:
        # ak.stock_fund_flow_individual() 这个是获取所有
        # 下面是获取指定个股的资金，也是有hexin-v这个header
        # 页面 https://stockpage.10jqka.com.cn/002580/funds/#funds_sszjlx
        # 接口 https://stockpage.10jqka.com.cn/spService/002580/Funds/realFunds/free/1/
        # 本接口还可以获取到行业资金流入、行业涨跌幅、行业资金流入/流出靠前个股
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
        _log_api_error(f"个股资金流 symbol={symbol!r}")
        return None


def _pk(symbol):
    try:
        return pk(symbol)
    except Exception:
        _log_api_error("盘口 | ak.stock_bid_ask_em")
        return None


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


def _hist(settings, symbol):
    # 历史行情 TODO
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
    hist_out = _rows_last_n_trade_days(hist_, n=30)
    return hist_out


async def _enrich_stock_list(
        settings: SettingsDep,
        fetch_stocks: Callable[..., Awaitable[list]],
        *,
        include_pre_snapshot: bool = False,
) -> list:
    out: list = []
    try:
        # fetch_stocks(settings): 与各列表拉取对齐，须支持 positional settings，勿写死 lambda 吞掉签名
        rows = await fetch_stocks(settings)
        for item in rows:
            try:
                symbol = item["股票代码"]
            except Exception:
                _log_api_error("获取股票代码")
                continue

            jbxx_ = _jbxx(symbol)
            if jbxx_:
                item["总股本"] = jbxx_["总股本"]
                item["流通股"] = jbxx_["流通股"]
                item["总市值"] = jbxx_["总市值"]
                item["流通市值"] = jbxx_["流通市值"]
                item["上市时间"] = jbxx_["上市时间"]

            pk_raw = _pk(symbol)
            item["盘口"] = pk_raw if isinstance(pk_raw, dict) else {}

            hist_ = _hist(settings, symbol)
            item["历史行情"] = hist_

            if settings.QUANT_ARCHIVE_ENABLED:
                tzh = load_computed_metrics_zh(settings, symbol)
                if tzh:
                    item["技术指标"] = tzh

            if include_pre_snapshot:
                pm = pre_auction_minute_zh("分钟行情 | ak.stock_zh_a_hist_pre_min_em", symbol)
                item["分钟行情"] = pm if isinstance(pm, list) else []

            zj_raw = await _ggzjl(symbol)
            item["个股资金流"] = zj_raw

            stock_name = item.get("股票名称")
            concepts = await _fetch_stock_concepts_wcxg(
                symbol,
                stock_name if isinstance(stock_name, str) else None,
            )
            if concepts:
                item["所属概念"] = concepts
                item["概念来源"] = "问财"

            out.append(item)
    except Exception:
        _log_api_error("_enrich_stock_list")
        out = []
    return out


async def _async_optional_rows(_settings: SettingsDep) -> list:
    return await run_in_threadpool(lambda: _load_stock_rows_from_quant_file(QUANT_OPTIONAL_FILENAME))


async def _async_holding_rows(_settings: SettingsDep) -> list:
    return await run_in_threadpool(lambda: _load_stock_rows_from_quant_file(QUANT_HOLDING_FILENAME))


async def _enrich_optional_and_holding(settings: SettingsDep) -> tuple[list, list]:
    """自选 + 持仓两行列表，结构与原先两次 ``_enrich_stock_list`` 调用一致。"""
    zxg = await _enrich_stock_list(settings, _async_optional_rows, include_pre_snapshot=True)
    ccg = await _enrich_stock_list(settings, _async_holding_rows, include_pre_snapshot=True)
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
    if limit is None:
        return rows
    return rows[:limit]


async def _ztgk(settings: SettingsDep, more: bool = False):
    result: dict[str, Any] = {}
    row_limit = settings.quant_bulk_row_limit()
    try:
        zt_full = await run_in_threadpool(ztgc)
        height = _zt_height(zt_full)
        result["今日涨停"] = _apply_row_limit(zt_full, row_limit)
        result["市场高度"] = f"{height}连板"
    except Exception:
        _log_api_error("今日涨停股全量 | ztgc")

    if more:
        try:
            zrzt = ztgc_with_date(get_n_workdays_ago(n=1))
            result["昨日涨停"] = _apply_row_limit(zrzt, row_limit)
        except Exception:
            _log_api_error("昨日涨停股池全量 | ztgc_with_date")

    return result


async def _hot(settings: SettingsDep):
    try:
        n = settings.quant_hot_list_limit()
        raw_hot = await hot_stock(settings, n)
        rows = raw_hot[:n] if isinstance(raw_hot, list) else []
        out: list[dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            symbol = item.get("股票代码")
            if symbol:
                stock_name = item.get("股票名称")
                concepts = await _fetch_stock_concepts_wcxg(
                    str(symbol),
                    stock_name if isinstance(stock_name, str) else None,
                )
                if concepts:
                    item = {**item, "所属概念": concepts, "概念来源": "问财"}
                elif item.get("所属概念"):
                    item = {**item, "概念来源": "人气榜"}
            out.append(item)
        return out
    except Exception:
        _log_api_error("同花顺人气股 | ths.hot_stock (no enrich)")
        return []


async def _pkyd(settings: SettingsDep):
    """东财盘口异动（60日新高 / 60日大幅上涨），按代码去重后附带问财所属概念。

    仅保留沪主/深主/创业板（60/00/30），剔除科创板、北交所等。
    """
    try:
        entries: list[tuple[str, str | None, str]] = []
        for label in ("60日新高", "60日大幅上涨"):
            batch = pkyd(label)
            if not isinstance(batch, list):
                continue
            for row in batch:
                if not isinstance(row, dict):
                    continue
                code = normalize_a_share_code(row.get("代码", ""))
                if not code or not is_allowed_symbol_pool_code(code):
                    continue
                entries.append((code, row.get("名称"), label))

        merged = merge_pkyd_rows_by_code(entries=entries)
        merged = [
            item
            for item in merged
            if isinstance(item, dict)
            and is_allowed_symbol_pool_code(item.get("股票代码", ""))
        ]
        row_limit = settings.quant_bulk_row_limit()
        if row_limit is not None:
            merged = merged[:row_limit]
        concept_cache: dict[str, list[str] | None] = {}
        for item in merged:
            symbol = str(item.get("股票代码", "")).strip()
            name = item.get("股票名称")
            concepts = await _fetch_stock_concepts_wcxg(
                symbol,
                name if isinstance(name, str) else None,
                cache=concept_cache,
            )
            if concepts:
                item["所属概念"] = concepts
                item["概念来源"] = "问财"
        return merged
    except Exception:
        _log_api_error("盘口异动 | dfcf.pkyd")
        return None


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

    news: list = []

    # 全球财经资讯
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
    try:
        cls_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_cls))
        if cls_data:
            for d in cls_data:
                if get_val(d, "标题", "") or get_val(d, "摘要", ""):
                    news.append(
                        {
                            "标题": get_val(d, "标题"),
                            "摘要": get_val(d, "摘要"),
                            "发布时间": _combine_cls_publish_datetime(
                                get_val(d, "发布日期"),
                                get_val(d, "发布时间"),
                            ),
                            "来源": "财联社",
                        }
                    )
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_cls")

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
    # 大盘指数
    dpzs_ = await _dpzs()

    # 赚钱效应
    zqxy_ = await _zqxy(market_phase="intraday")

    # 涨停概况
    ztgk_ = await _ztgk(settings)

    zxg_, ccg_ = await _enrich_optional_and_holding(settings)

    result = {
        "大盘指数": dpzs_,
        "赚钱效应": zqxy_,
        "涨停概况": ztgk_,
        "自选股": zxg_,
        "持仓股": ccg_,
    }

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

    # 大盘指数
    dpzs = await _dpzs()

    # 赚钱效应
    zqxy_ = await _zqxy(market_phase="intraday")

    # 涨幅前十概念
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
    zttj = await _ztgk(settings, True)

    # 人气股
    hot_ = await _hot(settings)

    zxg_, ccg_ = await _enrich_optional_and_holding(settings)

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy_,
        "概念板块": gn_bk,
        "涨停统计": zttj,
        "同花顺人气榜": hot_,
        "自选股": zxg_,
        "持仓股": ccg_,
    }
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
    # 大盘指数
    dpzs = await _dpzs()

    # 赚钱效应
    zqxy_ = await _zqxy(market_phase="intraday")

    # 涨幅前十概念
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
    zttj = await _ztgk(settings, True)

    # 自选、持仓
    zxg_, ccg_ = await _enrich_optional_and_holding(settings)

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy_,
        "概念板块": gn_bk,
        "涨停统计": zttj,
        "自选股": zxg_,
        "持仓股": ccg_,
    }
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
    # 大盘指数
    dpzs = await _dpzs()

    # 赚钱效应
    zqxy_ = await _zqxy(market_phase="closed")

    # 大盘资金流
    zjl = await zjl_(3)

    # 涨幅前十概念
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

    # 涨停概况
    zttj = await _ztgk(settings, True)

    # 人气股
    hot_ = await _enrich_stock_list(settings, _hot, include_pre_snapshot=True)

    # 盘口异动：问财所属概念 + 与人气/涨停交叉打标
    pkyd_ = await _pkyd(settings)
    pkyd_list = pkyd_ if isinstance(pkyd_, list) else []
    tag_map = build_pkyd_tag_map(pkyd_list)
    hot_ = enrich_list_with_pkyd_tags(hot_, tag_map)
    zttj = enrich_zt_stats_with_pkyd(zttj, tag_map)

    zxg_, ccg_ = await _enrich_optional_and_holding(settings)

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy_,
        "大盘资金流": zjl,
        "概念板块": gn_bk,
        "涨停统计": zttj,
        "同花顺人气榜": hot_,
        "盘口异动": pkyd_list,
        "自选股": zxg_,
        "持仓股": ccg_,
    }
    return Response(data=_finalize_quant_payload(result))
