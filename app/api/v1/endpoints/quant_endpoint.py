"""openclaw量化数据入口"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.schemas.response import Response
from app.utils.common_util import list_to_dict
from app.utils.dataframe import dataframe_to_records
from app.utils.dfcf_util import ztgc, jbxx, pk, xw
from app.utils.ths_util import stock_fund_flow_concept, hot_stock, stock_skyrocket

router = APIRouter(tags=["量化入口"])


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
    # 财经早餐
    try:
        cjzc = dataframe_to_records(await run_in_threadpool(ak.stock_info_cjzc_em))
    except:
        cjzc = None

    # 全球财经快讯（东方财富）
    cjzx = []
    try:
        qqcjzx = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_em))
        cjzx.append(qqcjzx)
    except:
        qqcjzx = None

    # 全球财经快讯（新浪）
    try:
        qqcjzxxl = dataframe_to_records(await run_in_threadpool(ak.stock_info_global_sina))
        cjzx.append(qqcjzxxl)
    except:
        qqcjzxxl = None

    # 东方财富大盘指数（实时）
    try:
        dpzs = [item for item in dataframe_to_records(
            await run_in_threadpool(lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"))) if
                item["序号"] in [1, 2, 4]]
    except:
        dpzs = None

    # 赚钱效应分析（是不是实时？）
    try:
        zqxy = list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_market_activity_legu)))
    except:
        zqxy = None

    result = {
        "财经早餐": cjzc,
        "全球财经资讯": cjzx,
        "大盘指数": dpzs,
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
    try:
        # 东方财富大盘指数（实时）
        try:
            dpzs = [item for item in dataframe_to_records(
                await run_in_threadpool(lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"))) if
                    item["序号"] in [1, 2, 4]]
        except:
            dpzs = None

        # 赚钱效应（是不是实时？）
        try:
            zqxy = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.stock_market_activity_legu())))
        except:
            zqxy = None

        # 今日涨幅前十概念（不获取涨幅前十股票，这样会让大模型先入为主，其他同）
        try:
            jrzfqsgn = await stock_fund_flow_concept('即时', '行业-涨跌幅')
        except:
            jrzfqsgn = None

        # 今日资金流入前十概念
        try:
            jrzjlrqsgn = await stock_fund_flow_concept('即时', '流入资金')
        except:
            jrzjlrqsgn = None

        # 涨停统计
        try:
            zttj = ztgc()
        except:
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
                    item['盘口'] = pk_
                    item['新闻'] = xw_
                    item['基本信息'] = jbxx_
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
                    item['盘口'] = pk_
                    item['新闻'] = xw_
                    item['基本信息'] = jbxx_
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
            "今日涨幅前十概念": jrzfqsgn,
            "今日资金流入前十概念": jrzjlrqsgn,
            "涨停统计": zttj,
            "同花顺人气股": thsrqg,
            "人气飙升榜": thsrqbsb
        }

    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=result)


@router.get(
    "/quant/market/post_market",
    response_model=Response,
    summary="盘后",
    description="盘后",
)
async def post_market(settings: SettingsDep) -> Response:
    # 当日大盘涨跌情况（近实时）
    try:
        dpzd = list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_market_activity_legu)))
    except:
        dpzd = None

    # 近三个交易日大盘资金流入情况（非实时？）
    try:
        dpzj = dataframe_to_records(await run_in_threadpool(ak.stock_market_fund_flow))[-3:]
    except:
        dpzj = None

    # 指数历史K线（有问题）(关键能否这样获取？)（或者每次获取完之后保存下来，自己就有历史涨跌数据了？）
    try:
        lskx = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.index_zh_a_hist(symbol="000001"))))
    except:
        lskx = None

    # 今日涨幅前十概念
    try:
        jrzfqsgn = await stock_fund_flow_concept('即时', '行业-涨跌幅')
    except:
        jrzfqsgn = None

    # 近3日资金流入概念
    try:
        gnzjlr = await stock_fund_flow_concept('3日排行', '流入资金')
    except:
        gnzjlr = None

    # 涨停统计
    try:
        zttj = ztgc()
    except:
        zttj = None

    result = {
        "今日大盘涨跌情况": dpzd,
        "近三个交易日大盘资金流入情况": dpzj,
        "指数历史K线": lskx,
        "今日涨幅前十概念": jrzfqsgn,
        "近3日资金流入概念": gnzjlr,
        "涨停统计": zttj
    }

    # 获取自选、持仓、飙升榜，然后得到个股概况、龙虎榜

    # 个股龙虎榜买入
    # gglhbmr = dataframe_to_records(
    #     await run_in_threadpool(lambda: ak.stock_lhb_stock_detail_em(symbol="002580", date=today(), flag='买入')))

    # 个股龙虎榜卖出
    # gglhbmr = dataframe_to_records(
    #     await run_in_threadpool(lambda: ak.stock_lhb_stock_detail_em(symbol="002580", date=today(), flag='卖出')))

    return Response(data=result)
