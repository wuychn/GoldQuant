"""openclaw量化数据入口"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import akshare as ak
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.schemas.response import Response
from app.utils.common_util import list_to_dict, today, get_val, cal_avg
from app.utils.dataframe import dataframe_to_records
from app.utils.dfcf_util import cmfb, hsgtzj, jbxx, lhbxq, pk, zj, ztgc, hist
from app.utils.ths_util import stock_fund_flow_concept, hot_stock, stock_skyrocket

logger = logging.getLogger(__name__)

router = APIRouter(tags=["量化入口"])

_INDEX_SERIAL_WHITELIST = (1, 2, 4)


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


async def _enrich_ths_stock_list(
        settings: SettingsDep,
        fetch_stocks: Callable[..., Awaitable[list]],
        *,
        with_lhb: bool,
        list_context: str,
) -> list:
    out: list = []
    try:
        rows = await fetch_stocks(settings=settings)
        rows = rows[:10]
        for item in rows:
            try:
                symbol = item["股票代码"]
            except Exception:
                _log_api_error(f"{list_context} read_symbol item_keys={list(item)!r}")
                continue

            jbxx_ = _sync_call_or_none(
                f"{list_context} dfcf.jbxx symbol={symbol!r}",
                lambda: jbxx(symbol),
            )
            pk_ = _sync_call_or_none(
                f"{list_context} dfcf.pk symbol={symbol!r}",
                lambda: pk(symbol),
            )
            zj_ = _sync_call_or_none(
                f"{list_context} dfcf.zj symbol={symbol!r}",
                lambda: zj(symbol),
            )
            cmfb_ = _sync_call_or_none(
                f"{list_context} dfcf.cmfb symbol={symbol!r}",
                lambda: cmfb(symbol),
            )

            # 历史行情（日线）
            hist_ = _sync_call_or_none(
                f"{list_context} dfcf.hist symbol={symbol!r}",
                lambda: hist(symbol),
            )

            # 计算五日均价
            avg_5 = None
            hist_5 = hist_[-5:]
            if hist_5 and len(hist_5) >= 5:
                avg_5 = cal_avg(hist_5, '收盘')

            # 计算10日均价
            avg_10 = None
            hist_10 = hist_
            if hist_10 and len(hist_10) >= 5:
                avg_10 = cal_avg(hist_10, '收盘')

            item["盘口"] = pk_
            item["基本信息"] = jbxx_
            item["资金流入流出"] = zj_
            item["筹码分布"] = cmfb_
            item["历史行情"] = hist_
            if avg_5:
                item["5日线"] = avg_5
            if avg_10:
                item["10日线"] = avg_10

            if with_lhb:
                lhbmr = _sync_call_or_none(
                    f"{list_context} dfcf.lhbxq buy symbol={symbol!r}",
                    lambda: lhbxq(symbol, today(), "买入"),
                )
                lhbmc = _sync_call_or_none(
                    f"{list_context} dfcf.lhbxq sell symbol={symbol!r}",
                    lambda: lhbxq(symbol, today(), "卖出"),
                )
                item["龙虎榜"] = {"买入": lhbmr, "卖出": lhbmc}

            out.append(item)
    except Exception:
        _log_api_error(f"{list_context}.fetch_list")
        out = []
    return out


async def _zx(settings):
    # 从 ~/data/quant/optional.md 获取自选股，自选股格式 [{"股票代码": "xx股份", "股票代码": "xxxx"}]
    return [{"股票代码": "002580", "股票名称": "圣阳股份"}]


async def _cc(settings):
    # 从 ~/data/quant/holding.md 获取持仓股，持仓股格式 [{"股票代码": "xx股份", "股票代码": "xxxx", "买入时间": "xxxx-xx-xx xx:xx:xx", "买入价格": "xxxx"}]
    return [{"股票代码": "002580", "股票名称": "圣阳股份"}]


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
                        "发布时间": get_val(d, '发布时间'),
                        "链接": get_val(d, '链接'),
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
                        "链接": get_val(d, '链接'),
                        "来源": '同花顺'
                    })
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_ths")

    # 财联社电报
    try:
        cls_data = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_cls))
        if cls_data:
            for d in cls_data:
                if get_val(d, '标题') or get_val(d, '摘要'):
                    news.append({
                        "标题": get_val(d, '标题'),
                        "摘要": get_val(d, '摘要'),
                        "发布时间": get_val(d, '发布日期').replace('T00:00:00.000', ' ') + get_val(d, '发布时间'),
                        "链接": '',
                        "来源": '财联社'
                    })
    except Exception:
        _log_api_error("GET /quant/market/news | ak.stock_info_global_cls")

    return Response(data=news)


@router.get(
    "/quant/market/pre_market",
    response_model=Response,
    summary="盘前",
    description="盘前",
)
async def pre_market(settings: SettingsDep) -> Response:
    """
    交易日早上9：25执行

    1、隔夜美股
    2、日韩股市
    3、全球要闻
    4、集合竞价（三大指数开盘情况，包括涨跌，张蝶数，概念等）
    5、给出判断
    6、保存到当日盘前数据，避免再次调用接口
    """
    route = "GET /quant/market/pre_market"
    dpzs = await _important_index_spot(f"{route} | ak.stock_zh_index_spot_em")
    # TODO 开盘时获取到的是昨天的数据
    # zjl = await _last_market_fund_flow_row(f"{route} | ak.stock_market_fund_flow")
    # zqxy = await _earning_effect_pre_market(f"{route} | ak.stock_market_activity_legu")

    # 从 ~/data/quant/optional.md 获取自选股，自选股格式 [{"股票代码": "xx股份", "股票代码": "xxxx"}]
    zxg = await _enrich_ths_stock_list(
        settings,
        _zx,
        with_lhb=False,
        list_context=f"{route} | _zx",
    )

    # 从 ~/data/quant/holding.md 获取持仓股，持仓股格式 [{"股票代码": "xx股份", "股票代码": "xxxx", "买入时间": "xxxx-xx-xx xx:xx:xx", "买入价格": "xxxx"}]
    ccg = await _enrich_ths_stock_list(
        settings,
        _cc,
        with_lhb=False,
        list_context=f"{route} | _cc",
    )

    result = {
        "大盘指数": dpzs,
        "自选股": zxg,
        "持仓股": ccg,
    }
    return Response(data=result)


@router.get(
    "/quant/market/during_market",
    response_model=Response,
    summary="盘中",
    description="盘中",
)
async def during_market(settings: SettingsDep) -> Response:
    """
    1、大盘指数
    2、大盘资金流
    3、概念板块
    4、概念资金流
    5、自选/持仓/人气股/飙升榜以及个股的资金流
    """
    route = "GET /quant/market/during_market"
    dpzs = await _important_index_spot(f"{route} | ak.stock_zh_index_spot_em")

    # TODO 数据延迟太大，可能是昨天的数据
    zqxy = await _earning_effect_intraday(f"{route} | ak.stock_market_activity_legu()")
    zjl = await _last_market_fund_flow_row(f"{route} | ak.stock_market_fund_flow")

    jrzfqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
    )
    jrzjlrqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "流入资金",
    )
    zttj = await _ztgc_or_none(f"{route} | dfcf.ztgc")
    hsgtzjlx = await _hsgtzj_or_none(f"{route} | dfcf.hsgtzj")
    thsrqg = await _enrich_ths_stock_list(
        settings,
        hot_stock,
        with_lhb=False,
        list_context=f"{route} | ths.hot_stock",
    )
    thsrqbsb = await _enrich_ths_stock_list(
        settings,
        stock_skyrocket,
        with_lhb=False,
        list_context=f"{route} | ths.stock_skyrocket",
    )

    # 从 ~/data/quant/optional.md 获取自选股，自选股格式 [{"股票代码": "xx股份", "股票代码": "xxxx"}]
    zxg = await _enrich_ths_stock_list(
        settings,
        _zx,
        with_lhb=False,
        list_context=f"{route} | _zx",
    )

    # 从 ~/data/quant/holding.md 获取持仓股，持仓股格式 [{"股票代码": "xx股份", "股票代码": "xxxx", "买入时间": "xxxx-xx-xx xx:xx:xx", "买入价格": "xxxx"}]
    ccg = await _enrich_ths_stock_list(
        settings,
        _cc,
        with_lhb=False,
        list_context=f"{route} | _cc",
    )

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy,
        "大盘资金流": zjl,
        "沪深港通资金流向": hsgtzjlx,
        "今日涨幅前十概念": jrzfqsgn,
        "今日资金流入前十概念": jrzjlrqsgn,
        "涨停统计": zttj,
        "同花顺人气股": thsrqg,
        "人气飙升榜": thsrqbsb,
        "自选股": zxg,
        "持仓股": ccg,
    }
    return Response(data=result)


@router.get(
    "/quant/market/post_market",
    response_model=Response,
    summary="盘后",
    description="盘后",
)
async def post_market(settings: SettingsDep) -> Response:
    route = "GET /quant/market/post_market"
    dpzs = await _important_index_spot(f"{route} | ak.stock_zh_index_spot_em")
    zqxy = await _earning_effect_intraday(f"{route} | ak.stock_market_activity_legu()")
    zjl = await _last_market_fund_flow_row(f"{route} | ak.stock_market_fund_flow")
    jrzfqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "行业-涨跌幅",
    )
    jrzjlrqsgn = await _stock_fund_flow_concept_or_none(
        f"{route} | ths.stock_fund_flow_concept",
        "流入资金",
    )
    zttj = await _ztgc_or_none(f"{route} | dfcf.ztgc")
    hsgtzjlx = await _hsgtzj_or_none(f"{route} | dfcf.hsgtzj")
    thsrqg = await _enrich_ths_stock_list(
        settings,
        hot_stock,
        with_lhb=True,
        list_context=f"{route} | ths.hot_stock",
    )
    thsrqbsb = await _enrich_ths_stock_list(
        settings,
        stock_skyrocket,
        with_lhb=True,
        list_context=f"{route} | ths.stock_skyrocket",
    )

    # 从 ~/data/quant/optional.md 获取自选股，自选股格式 [{"股票代码": "xx股份", "股票代码": "xxxx"}]
    zxg = await _enrich_ths_stock_list(
        settings,
        _zx,
        with_lhb=False,
        list_context=f"{route} | _zx",
    )

    # 从 ~/data/quant/holding.md 获取持仓股，持仓股格式 [{"股票代码": "xx股份", "股票代码": "xxxx", "买入时间": "xxxx-xx-xx xx:xx:xx", "买入价格": "xxxx"}]
    ccg = await _enrich_ths_stock_list(
        settings,
        _cc,
        with_lhb=False,
        list_context=f"{route} | _cc",
    )

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy,
        "大盘资金流": zjl,
        "沪深港通资金流向": hsgtzjlx,
        "今日涨幅前十概念": jrzfqsgn,
        "今日资金流入前十概念": jrzjlrqsgn,
        "涨停统计": zttj,
        "同花顺人气股": thsrqg,
        "人气飙升榜": thsrqbsb,
        "自选股": zxg,
        "持仓股": ccg,
    }
    return Response(data=result)
