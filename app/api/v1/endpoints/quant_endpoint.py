"""openclaw量化数据入口"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.schemas.response import Response
from app.utils.common_util import list_to_dict, today
from app.utils.dataframe import dataframe_to_records
from app.utils.dfcf_util import ztgc, jbxx, pk, xw, zj, lhbxq
from app.utils.ths_util import stock_fund_flow_concept, hot_stock, stock_skyrocket

router = APIRouter(tags=["量化入口"])


@router.get(
    "/quant/market/news",
    response_model=Response,
    summary="新闻",
    description="新闻",
)
async def news():
    """
    新闻，每30分钟执行一次，使用AI总结后保存到当日重要资讯中
    """
    # 财经早餐
    try:
        cjzc = dataframe_to_records(await run_in_threadpool(ak.stock_info_cjzc_em))
    except Exception as e:
        print(e)
        cjzc = None

    # 全球财经快讯（东方财富）
    cjzx = []
    try:
        qqcjzx = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_em))
        cjzx.append(qqcjzx)
    except Exception as e:
        print(e)
        qqcjzx = None

    # 全球财经快讯（新浪）
    try:
        qqcjzxxl = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_sina))
        cjzx.append(qqcjzxxl)
    except Exception as e:
        print(e)
        qqcjzxxl = None

    # 财联社电报
    try:
        cls = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_cls))
    except Exception as e:
        print(e)
        cls = None

    result = {
        "财经早餐": cjzx,
        "全球财经快讯": cjzx,
        "财联社电报": cls
    }

    return Response(data=result)


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
    # 东方财富大盘指数（实时）
    try:
        dpzs = [item for item in dataframe_to_records(
            await run_in_threadpool(lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"))) if
                item["序号"] in [1, 2, 4]]
    except Exception as e:
        print(e)
        dpzs = None

    # 大盘资金流
    try:
        zjl = dataframe_to_records(await run_in_threadpool(ak.stock_market_fund_flow))[-1:]
    except Exception as e:
        print(e)
        zjl = None

    # 赚钱效应分析（近实时）
    try:
        zqxy = list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_market_activity_legu)))
    except Exception as e:
        print(e)
        zqxy = None

    result = {
        "大盘指数": dpzs,
        "大盘资金流": zjl,
        "赚钱效应": zqxy
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
    # 东方财富大盘指数（实时）
    try:
        dpzs = [item for item in dataframe_to_records(
            await run_in_threadpool(lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"))) if
                item["序号"] in [1, 2, 4]]
    except Exception as e:
        print(e)
        dpzs = None

    # 赚钱效应（是不是实时？）
    try:
        zqxy = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.stock_market_activity_legu())))
    except Exception as e:
        print(e)
        zqxy = None

    # 大盘资金流
    try:
        zjl = dataframe_to_records(await run_in_threadpool(ak.stock_market_fund_flow))[-1:]
    except Exception as e:
        print(e)
        zjl = None

    # 今日涨幅前十概念
    try:
        jrzfqsgn = await stock_fund_flow_concept('即时', '行业-涨跌幅')
    except Exception as e:
        print(e)
        jrzfqsgn = None

    # 今日资金流入前十概念
    try:
        jrzjlrqsgn = await stock_fund_flow_concept('即时', '流入资金')
    except Exception as e:
        print(e)
        jrzjlrqsgn = None

    # 涨停统计
    try:
        zttj = ztgc()
    except Exception as e:
        print(e)
        zttj = None

    # 获取同花顺人气股/人气飙升榜/自选/持仓
    thsrqg = []
    try:
        hot_stocks = await hot_stock(settings=settings)
        # 取前10
        hot_stocks = hot_stocks[:10]
        for item in hot_stocks:
            try:
                symbol = item['股票代码']
                # 基本信息
                jbxx_ = jbxx(symbol)
                pk_ = pk(symbol)
                xw_ = xw(symbol)
                zj_ = zj(symbol)
                item['盘口'] = pk_
                item['新闻'] = xw_
                item['基本信息'] = jbxx_
                item['资金流入流出'] = zj_
                thsrqg.append(item)
            except Exception as e:
                print(e)
    except Exception as e:
        print(e)
        thsrqg = []

    # 同花顺人气飙升榜
    thsrqbsb = []
    try:
        stock_skyrockets = await stock_skyrocket(settings=settings)
        # 取前10
        stock_skyrockets = stock_skyrockets[:10]
        for item in stock_skyrockets:
            try:
                symbol = item['股票代码']
                # 基本信息
                jbxx_ = jbxx(symbol)
                pk_ = pk(symbol)
                xw_ = xw(symbol)
                zj_ = zj(symbol)
                item['盘口'] = pk_
                item['新闻'] = xw_
                item['基本信息'] = jbxx_
                item['资金流入流出'] = zj_
                thsrqbsb.append(item)
            except Exception as e:
                print(e)
                pass
    except Exception as e:
        print(e)
        thsrqbsb = []

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy,
        "大盘资金流": zjl,
        "今日涨幅前十概念": jrzfqsgn,
        "今日资金流入前十概念": jrzjlrqsgn,
        "涨停统计": zttj,
        "同花顺人气股": thsrqg,
        "人气飙升榜": thsrqbsb,
        "自选股": None,
        "持仓股": None
    }
    return Response(data=result)


@router.get(
    "/quant/market/post_market",
    response_model=Response,
    summary="盘后",
    description="盘后",
)
async def post_market(settings: SettingsDep) -> Response:
    # 东方财富大盘指数（实时）
    try:
        dpzs = [item for item in dataframe_to_records(
            await run_in_threadpool(lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"))) if
                item["序号"] in [1, 2, 4]]
    except Exception as e:
        print(e)
        dpzs = None

    # 赚钱效应（是不是实时？）
    try:
        zqxy = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.stock_market_activity_legu())))
    except Exception as e:
        print(e)
        zqxy = None

    # 大盘资金流
    try:
        zjl = dataframe_to_records(await run_in_threadpool(ak.stock_market_fund_flow))[-1:]
    except Exception as e:
        print(e)
        zjl = None

    # 今日涨幅前十概念
    try:
        jrzfqsgn = await stock_fund_flow_concept('即时', '行业-涨跌幅')
    except Exception as e:
        print(e)
        jrzfqsgn = None

    # 今日资金流入前十概念
    try:
        jrzjlrqsgn = await stock_fund_flow_concept('即时', '流入资金')
    except Exception as e:
        print(e)
        jrzjlrqsgn = None

    # 涨停统计
    try:
        zttj = ztgc()
    except Exception as e:
        print(e)
        zttj = None

    # 获取同花顺人气股/人气飙升榜/自选/持仓
    thsrqg = []
    try:
        hot_stocks = await hot_stock(settings=settings)
        # 取前10
        hot_stocks = hot_stocks[:10]
        for item in hot_stocks:
            try:
                symbol = item['股票代码']
                # 基本信息
                jbxx_ = jbxx(symbol)
                pk_ = pk(symbol)
                xw_ = xw(symbol)
                zj_ = zj(symbol)
                lhbmr = lhbxq(symbol, today(), '买入')
                lhbmc = lhbxq(symbol, today(), '卖出')
                lhb = {
                    "买入": lhbmr,
                    "卖出": lhbmc
                }
                item['盘口'] = pk_
                item['新闻'] = xw_
                item['基本信息'] = jbxx_
                item['资金流入流出'] = zj_
                item['龙虎榜'] = lhb
                thsrqg.append(item)
            except Exception as e:
                print(e)
    except Exception as e:
        print(e)
        thsrqg = []

    # 同花顺人气飙升榜
    thsrqbsb = []
    try:
        stock_skyrockets = await stock_skyrocket(settings=settings)
        # 取前10
        stock_skyrockets = stock_skyrockets[:10]
        for item in stock_skyrockets:
            try:
                symbol = item['股票代码']
                # 基本信息
                jbxx_ = jbxx(symbol)
                pk_ = pk(symbol)
                xw_ = xw(symbol)
                zj_ = zj(symbol)
                lhbmr = lhbxq(symbol, today(), '买入')
                lhbmc = lhbxq(symbol, today(), '卖出')
                lhb = {
                    "买入": lhbmr,
                    "卖出": lhbmc
                }
                item['盘口'] = pk_
                item['新闻'] = xw_
                item['基本信息'] = jbxx_
                item['资金流入流出'] = zj_
                item['龙虎榜'] = lhb
                thsrqbsb.append(item)
            except Exception as e:
                print(e)
                pass
    except Exception as e:
        print(e)
        thsrqbsb = []

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy,
        "大盘资金流": zjl,
        "今日涨幅前十概念": jrzfqsgn,
        "今日资金流入前十概念": jrzjlrqsgn,
        "涨停统计": zttj,
        "同花顺人气股": thsrqg,
        "人气飙升榜": thsrqbsb,
        "自选股": None,
        "持仓股": None
    }
    return Response(data=result)
