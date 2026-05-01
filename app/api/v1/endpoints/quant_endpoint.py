"""openclaw量化数据入口"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections.abc import Awaitable, Callable
from numbers import Integral, Real
from pathlib import Path
from typing import Any

import akshare as ak
from fastapi import APIRouter, BackgroundTasks
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.core.config import Settings
from app.schemas.response import Response
from app.utils.common_util import (
    cal_avg,
    get_n_workdays_ago,
    get_val,
    is_real_workday_cn,
    list_to_dict,
)
from app.utils.dataframe import dataframe_to_records
from app.utils.dfcf_util import hsgtzj, pk, zj, ztgc, hist, xw
from app.utils.quant_archive import (
    archive_market_sync,
    daily_hist_fetch_start_date,
    load_computed_metrics_zh,
    load_merge_write_daily_bars,
)
from app.utils.quant_market_enrich import (
    build_market_state_machine_zh,
    pre_auction_minute_zh,
    spot_snapshot_for_codes,
    today_zt_pool_full_zh,
)
from app.utils.ths_util import stock_fund_flow_concept, hot_stock, stock_skyrocket

logger = logging.getLogger(__name__)

router = APIRouter(tags=["量化入口"])

_INDEX_SERIAL_WHITELIST = (1, 2, 4)
# 自选/持仓落盘：~/data/quant/ 下 JSONL（一行一条 JSON 对象）
QUANT_OPTIONAL_FILENAME = "optional.jsonl"
QUANT_HOLDING_FILENAME = "holding.jsonl"

# 东财五档经 ``pk`` 转写后键名多为「买1量…」「买一…」等形式；排除「买10」误匹配买1。
_PK_TIER_RE = re.compile(r"买[1-5](?![0-9])|买[一二三四五]|卖[1-5](?![0-9])|卖[一二三四五]")


def _yyyymmdd_to_iso(d8: str) -> str | None:
    s = str(d8).strip().replace("-", "").replace("/", "")
    if len(s) < 8 or not s[:8].isdigit():
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


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


def _slim_pk_bid_ask(pk: object) -> dict[str, Any] | None:
    """仅保留买一至买五、卖一至卖五相关键（兼容「买1」「买一」等东财转写）。"""
    if not isinstance(pk, dict) or not pk:
        return None
    out: dict[str, Any] = {}
    for k, v in pk.items():
        ks = str(k)
        if _PK_TIER_RE.search(ks):
            out[ks] = v
    return out if out else dict(list(pk.items())[:10])


def _slim_xw_list(xw: object, *, limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(xw, list):
        return []
    out: list[dict[str, Any]] = []
    for row in xw[:limit]:
        if not isinstance(row, dict):
            continue
        slim = {k: row[k] for k in ("标题", "发布时间") if k in row}
        if slim:
            out.append(slim)
    return out


def _slim_earning_effect_dict(zqxy: object, *, max_pairs: int = 22) -> dict[str, Any] | None:
    if not isinstance(zqxy, dict) or not zqxy:
        return None
    return dict(list(zqxy.items())[:max_pairs])


def _merge_concept_boards(jzf: list | None, jzj: list | None, *, limit: int = 8) -> dict[str, Any]:
    return {
        "涨幅榜": (jzf or [])[:limit],
        "资金流入榜": (jzj or [])[:limit],
    }


def _log_api_error(context: str) -> None:
    """记录上游/本地调用失败，带固定上下文便于定位，避免单点异常拖垮整次聚合。"""
    logger.exception("量化数据接口异常 [%s]", context)


def _sync_call_or_none(context: str, fn: Callable[[], object]) -> object | None:
    try:
        return fn()
    except Exception:
        _log_api_error(context)
        return None


async def _dataframe_records_or_none(context: str, fetch: Callable[..., object]) -> list | None:
    try:
        return dataframe_to_records(await run_in_threadpool(fetch))
    except Exception:
        _log_api_error(context)
        return None


async def _important_index_spot(context: str) -> list | None:
    try:
        raw = dataframe_to_records(
            await run_in_threadpool(lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"))
        )
        return [item for item in raw if item["序号"] in _INDEX_SERIAL_WHITELIST]
    except Exception:
        _log_api_error(context)
        return None


async def _last_market_fund_flow_row(context: str) -> list | None:
    try:
        return dataframe_to_records(await run_in_threadpool(ak.stock_market_fund_flow))[-1:]
    except Exception:
        _log_api_error(context)
        return None


async def _earning_effect_pre_market(context: str) -> dict | None:
    try:
        return list_to_dict(
            dataframe_to_records(await run_in_threadpool(ak.stock_market_activity_legu))
        )
    except Exception:
        _log_api_error(context)
        return None


async def _earning_effect_intraday(context: str) -> dict | None:
    try:
        return list_to_dict(
            dataframe_to_records(await run_in_threadpool(lambda: ak.stock_market_activity_legu()))
        )
    except Exception:
        _log_api_error(context)
        return None


async def _stock_fund_flow_concept_or_none(context: str, rank_by: str):
    try:
        return await stock_fund_flow_concept("即时", rank_by)
    except Exception:
        _log_api_error(f"{context} rank_by={rank_by!r}")
        return None


async def _ztgc_or_none(context: str):
    try:
        return ztgc()
    except Exception:
        _log_api_error(context)
        return None


async def _hsgtzj_or_none(context: str):
    try:
        return await run_in_threadpool(hsgtzj)
    except Exception:
        _log_api_error(context)
        return None


async def _market_bundle_zh(route: str) -> dict[str, object]:
    """仅返回「市场状态机」聚合块（涨停全表、昨日涨停明细不再下发，避免干扰模型）。"""
    zt_full = await run_in_threadpool(lambda: today_zt_pool_full_zh(f"{route}|今日涨停股池全量"))
    sc_zh = await run_in_threadpool(
        lambda: build_market_state_machine_zh(f"{route}|市场状态机", zt_pool_full=zt_full),
    )
    return {"市场状态机": sc_zh}


async def _enrich_ths_stock_list(
        settings: SettingsDep,
        fetch_stocks: Callable[..., Awaitable[list]],
        *,
        more: bool,
        list_context: str,
        include_pre_snapshot: bool = False,
) -> list:
    out: list = []
    try:
        rows = await fetch_stocks(settings=settings)
        rows = rows[:20]
        spot_effective: dict[str, dict[str, object]] = {}
        if include_pre_snapshot and settings.QUANT_SPOT_EM_FULL_TABLE:
            codes_set = {
                str(item.get("股票代码", "")).strip()
                for item in rows
                if item.get("股票代码")
            }
            if codes_set:
                cs = frozenset(codes_set)
                ctx = list_context
                spot_effective = await run_in_threadpool(
                    lambda: spot_snapshot_for_codes(f"{ctx}|盘前实时快照", set(cs)),
                )
        for item in rows:
            try:
                symbol = item["股票代码"]
            except Exception:
                _log_api_error(f"{list_context} read_symbol item_keys={list(item)!r}")
                continue

            # jbxx_ = _sync_call_or_none(
            #     f"{list_context} dfcf.jbxx symbol={symbol!r}",
            #     lambda: jbxx(symbol),
            # )
            pk_raw = _sync_call_or_none(
                f"{list_context} dfcf.pk symbol={symbol!r}",
                lambda: pk(symbol),
            )
            zj_raw = _sync_call_or_none(
                f"{list_context} dfcf.zj symbol={symbol!r}",
                lambda: zj(symbol),
            )

            if settings.QUANT_ARCHIVE_ENABLED:
                start_d = daily_hist_fetch_start_date(settings, symbol)
                hist_api = _sync_call_or_none(
                    f"{list_context} dfcf.hist symbol={symbol!r}",
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
                    f"{list_context} dfcf.hist symbol={symbol!r}",
                    _hist_no_archive,
                )

            if not hist_:
                hist_ = []

            avg_5: float | None = None
            hist_5 = hist_[-5:]
            if hist_5 and len(hist_5) >= 5:
                avg_5 = cal_avg(hist_5, "收盘")

            avg_10: float | None = None
            hist_10 = hist_[-10:]
            if hist_10 and len(hist_10) >= 10:
                avg_10 = cal_avg(hist_10, "收盘")

            hist_out = _rows_last_n_trade_days(hist_, n=30)

            item["盘口"] = _slim_pk_bid_ask(pk_raw)
            zj_list = zj_raw if isinstance(zj_raw, list) else []
            item["资金流入流出"] = _rows_last_n_trade_days(zj_list, n=3)
            item["历史行情"] = hist_out

            tzh: dict[str, Any] | None = None
            if settings.QUANT_ARCHIVE_ENABLED:
                tzh = load_computed_metrics_zh(settings, symbol)
                if tzh:
                    item["技术指标"] = tzh

            if not (isinstance(tzh, dict) and tzh.get("均线5日") is not None) and avg_5 is not None:
                item["5日线"] = avg_5
            if not (isinstance(tzh, dict) and tzh.get("均线10日") is not None) and avg_10 is not None:
                item["10日线"] = avg_10

            if include_pre_snapshot:
                pm = pre_auction_minute_zh(f"{list_context}|集合竞价分钟", symbol)
                if pm is None:
                    item["集合竞价分钟行情"] = []
                elif isinstance(pm, list):
                    item["集合竞价分钟行情"] = pm[-5:] if len(pm) > 5 else pm
                else:
                    item["集合竞价分钟行情"] = []
                snap = spot_effective.get(str(symbol).strip())
                if snap:
                    item["盘前实时快照"] = snap

            if more:
                xw_ = _sync_call_or_none(
                    f"{list_context} dfcf.xw symbol={symbol!r}",
                    lambda: xw(symbol),
                )
                item["个股新闻"] = _slim_xw_list(xw_)

            out.append(item)
    except Exception:
        _log_api_error(f"{list_context}.fetch_list")
        out = []
    return out


def _quant_data_file(name: str) -> Path:
    return Path.home() / "data" / "quant" / name


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


def _load_stock_rows_from_quant_file(filename: str) -> list:
    """读取 ``~/data/quant/{filename}``，仅支持 JSONL（一行一条 JSON）。"""
    path = _quant_data_file(filename)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        _log_api_error(f"quant read file path={path!s}")
        return []
    return _parse_jsonl_stock_text(text)


_NOT_TRADING_DAY_RESPONSE = Response(code=1, message="今天不是交易日", data=None)


def _schedule_quant_archive(
    background_tasks: BackgroundTasks,
    settings: Settings,
    phase: str,
    result: dict,
) -> None:
    if not getattr(settings, "QUANT_ARCHIVE_ENABLED", True):
        return
    try:
        payload = copy.deepcopy(result)
    except Exception:
        logger.exception("量化归档跳过：result 无法深拷贝 phase=%s", phase)
        return
    background_tasks.add_task(archive_market_sync, phase, payload, settings)


async def _guard_real_workday_or_non_trading_response() -> Response | None:
    ok = await run_in_threadpool(is_real_workday_cn)
    if not ok:
        return _NOT_TRADING_DAY_RESPONSE
    return None


async def _zx(settings):
    """从 ``~/data/quant/optional.jsonl`` 读取自选股（一行一条 JSON）。"""
    return await run_in_threadpool(lambda: _load_stock_rows_from_quant_file(QUANT_OPTIONAL_FILENAME))


async def _cc(settings):
    """从 ``~/data/quant/holding.jsonl`` 读取持仓（一行一条 JSON）。"""
    return await run_in_threadpool(lambda: _load_stock_rows_from_quant_file(QUANT_HOLDING_FILENAME))


@router.get(
    "/quant/market/news",
    response_model=Response,
    summary="新闻",
    description="新闻",
)
async def news():
    """
    新闻
    """

    news: list = []

    # 全球财经资讯
    try:
        dfcf_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_em))
        if dfcf_data:
            for d in dfcf_data:
                if get_val(d, '标题') or get_val(d, '摘要'):
                    news.append({
                        "标题": get_val(d, '标题'),
                        "摘要": get_val(d, '摘要'),
                        # "发布时间": get_val(d, '发布时间'),
                        # "链接": get_val(d, '链接'),
                        "来源": '东方财富'
                    })
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_em")

    # 同花顺财经
    try:
        ths_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_ths))
        if ths_data:
            for d in ths_data:
                if get_val(d, '标题') or get_val(d, '内容'):
                    news.append({
                        "标题": get_val(d, '标题'),
                        "摘要": get_val(d, '内容'),
                        "发布时间": get_val(d, '发布时间'),
                        # "链接": get_val(d, '链接'),
                        "来源": '同花顺'
                    })
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_ths")

    # 财联社电报
    try:
        cls_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_cls))
        if cls_data:
            for d in cls_data:
                if get_val(d, '标题', '') or get_val(d, '摘要', ''):
                    news.append({
                        "标题": get_val(d, '标题'),
                        "摘要": get_val(d, '摘要'),
                        "发布时间": get_val(d, '发布日期').replace('T00:00:00.000', ' ') + get_val(d, '发布时间'),
                        # "链接": '',
                        "来源": '财联社'
                    })
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_cls")

    return Response(data=_round_floats_for_api(news))


@router.get(
    "/quant/market/pre_market",
    response_model=Response,
    summary="盘前",
    description="盘前",
)
async def pre_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    """
    交易日早上9：25执行

    1、隔夜美股
    2、日韩股市
    3、全球要闻
    4、集合竞价（三大指数开盘情况，包括涨跌，张蝶数，概念等）
    5、给出判断
    6、保存到当日盘前数据，避免再次调用接口
    """
    # if (blocked := await _guard_real_workday_or_non_trading_response()) is not None:
    #     return blocked
    route = "GET /quant/market/pre_market"
    dpzs = await _important_index_spot(f"{route} | ak.stock_zh_index_spot_em")
    # TODO 开盘时获取到的是昨天的数据
    # zjl = await _last_market_fund_flow_row(f"{route} | ak.stock_market_fund_flow")
    # zqxy = await _earning_effect_pre_market(f"{route} | ak.stock_market_activity_legu")

    # 从 ~/data/quant/optional.jsonl 获取自选股（每行 {"股票代码","股票名称",...}）
    zxg = await _enrich_ths_stock_list(
        settings,
        _zx,
        more=False,
        list_context=f"{route} | _zx",
        include_pre_snapshot=True,
    )

    # 从 ~/data/quant/holding.jsonl 获取持仓股（每行一条 JSON，含 股票代码、买入时间 等）
    ccg = await _enrich_ths_stock_list(
        settings,
        _cc,
        more=False,
        list_context=f"{route} | _cc",
        include_pre_snapshot=True,
    )

    bundle = await _market_bundle_zh(route)
    result = {
        "大盘指数": dpzs,
        "自选股": zxg,
        "持仓股": ccg,
        **bundle,
    }
    _schedule_quant_archive(background_tasks, settings, "pre", result)
    return Response(data=_round_floats_for_api(copy.deepcopy(result)))


@router.get(
    "/quant/market/during_market",
    response_model=Response,
    summary="盘中",
    description="盘中",
)
async def during_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    """
    1、大盘指数
    2、大盘资金流
    3、概念板块
    4、概念资金流
    5、自选/持仓/人气股/飙升榜以及个股的资金流
    """
    # if (blocked := await _guard_real_workday_or_non_trading_response()) is not None:
    #     return blocked
    route = "GET /quant/market/during_market"
    dpzs = await _important_index_spot(f"{route} | ak.stock_zh_index_spot_em")

    # TODO 数据延迟太大，可能是昨天的数据
    zqxy_raw = await _earning_effect_intraday(f"{route} | ak.stock_market_activity_legu()")
    zqxy = _slim_earning_effect_dict(zqxy_raw)
    zjl = await _last_market_fund_flow_row(f"{route} | ak.stock_market_fund_flow")

    jrzfqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
    )
    jrzjlrqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "流入资金",
    )
    gn_bk = _merge_concept_boards(jrzfqsgn, jrzjlrqsgn)
    zttj = await _ztgc_or_none(f"{route} | dfcf.ztgc")
    # hsgtzjlx = await _hsgtzj_or_none(f"{route} | dfcf.hsgtzj")
    # 盘中不获取人气股
    # thsrqg = await _enrich_ths_stock_list(
    #     settings,
    #     hot_stock,
    #     more=False,
    #     list_context=f"{route} | ths.hot_stock",
    # )
    # thsrqbsb = await _enrich_ths_stock_list(
    #     settings,
    #     stock_skyrocket,
    #     more=False,
    #     list_context=f"{route} | ths.stock_skyrocket",
    # )

    # 从 ~/data/quant/optional.jsonl 获取自选股（每行 {"股票代码","股票名称",...}）
    zxg = await _enrich_ths_stock_list(
        settings,
        _zx,
        more=False,
        list_context=f"{route} | _zx",
    )

    # 从 ~/data/quant/holding.jsonl 获取持仓股（每行一条 JSON，含 股票代码、买入时间 等）
    ccg = await _enrich_ths_stock_list(
        settings,
        _cc,
        more=False,
        list_context=f"{route} | _cc",
    )

    bundle = await _market_bundle_zh(route)
    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy,
        "大盘资金流": zjl,
        # "沪深港通资金流向": hsgtzjlx,
        "概念板块": gn_bk,
        "涨停统计": zttj,
        # "同花顺人气股": thsrqg,
        # "人气飙升榜": thsrqbsb,
        "自选股": zxg,
        "持仓股": ccg,
        **bundle,
    }
    _schedule_quant_archive(background_tasks, settings, "during", result)
    return Response(data=_round_floats_for_api(copy.deepcopy(result)))


@router.get(
    "/quant/market/post_market",
    response_model=Response,
    summary="盘后",
    description="盘后",
)
async def post_market(settings: SettingsDep, background_tasks: BackgroundTasks) -> Response:
    # if (blocked := await _guard_real_workday_or_non_trading_response()) is not None:
    #     return blocked
    route = "GET /quant/market/post_market"
    dpzs = await _important_index_spot(f"{route} | ak.stock_zh_index_spot_em")
    zqxy_raw = await _earning_effect_intraday(f"{route} | ak.stock_market_activity_legu()")
    zqxy = _slim_earning_effect_dict(zqxy_raw)
    zjl = await _last_market_fund_flow_row(f"{route} | ak.stock_market_fund_flow")
    jrzfqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
    )
    jrzjlrqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "流入资金",
    )
    gn_bk = _merge_concept_boards(jrzfqsgn, jrzjlrqsgn)
    zttj = await _ztgc_or_none(f"{route} | dfcf.ztgc")
    # hsgtzjlx = await _hsgtzj_or_none(f"{route} | dfcf.hsgtzj")
    thsrqg = await _enrich_ths_stock_list(
        settings,
        hot_stock,
        more=True,
        list_context=f"{route} | ths.hot_stock",
    )
    # thsrqbsb = await _enrich_ths_stock_list(
    #     settings,
    #     stock_skyrocket,
    #     more=True,
    #     list_context=f"{route} | ths.stock_skyrocket",
    # )

    # 从 ~/data/quant/optional.jsonl 获取自选股（每行 {"股票代码","股票名称",...}）
    zxg = await _enrich_ths_stock_list(
        settings,
        _zx,
        more=False,
        list_context=f"{route} | _zx",
    )

    # 从 ~/data/quant/holding.jsonl 获取持仓股（每行一条 JSON，含 股票代码、买入时间 等）
    ccg = await _enrich_ths_stock_list(
        settings,
        _cc,
        more=False,
        list_context=f"{route} | _cc",
    )

    bundle = await _market_bundle_zh(route)
    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy,
        "大盘资金流": zjl,
        # "沪深港通资金流向": hsgtzjlx,
        "概念板块": gn_bk,
        "涨停统计": zttj,
        "同花顺人气股": thsrqg,
        # "人气飙升榜": thsrqbsb,
        "自选股": zxg,
        "持仓股": ccg,
        **bundle,
    }
    _schedule_quant_archive(background_tasks, settings, "post", result)
    return Response(data=_round_floats_for_api(copy.deepcopy(result)))
