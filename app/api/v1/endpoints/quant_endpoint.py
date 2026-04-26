"""openclaw量化数据入口"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.schemas.response import Response
from app.utils.common_util import list_to_dict
from app.utils.dataframe import dataframe_to_records
from app.utils.dfcf_util import ztgc
from app.utils.ths_util import stock_fund_flow_concept

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

        result = {
            "大盘指数": dpzs,
            "赚钱效应": zqxy,
            "今日涨幅前十概念": jrzfqsgn,
            "今日资金流入前十概念": jrzjlrqsgn,
            "涨停统计": zttj
        }

        # 涨停池/连扳股（从结果筛选？）
        # ztgc = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.stock_zt_pool_em(date="20260424"))))

        # 个股基本信息
        # ggjbxx = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.stock_individual_info_em(symbol="002580"))))

        # 个股买卖报价（买卖盘口）
        # ggmmbj = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.stock_bid_ask_em(symbol="002580"))))

        # 个股历史行情
        # gglshq = dataframe_to_records(await run_in_threadpool(lambda: ak.stock_zh_a_hist(symbol="002580", start_date="20260420", end_date="20260424")))

        # 个股龙虎榜日期
        # gglhbrq = dataframe_to_records(await run_in_threadpool(lambda: ak.stock_lhb_stock_detail_date_em(symbol="002580")))

        # 个股龙虎榜详情
        # gglhbmr = dataframe_to_records(await run_in_threadpool(lambda: ak.stock_lhb_stock_detail_em(symbol="002580", date="20260423", flag='买入')))
        # gglhbmc = dataframe_to_records(await run_in_threadpool(lambda: ak.stock_lhb_stock_detail_em(symbol="002580", date="20260423", flag='卖出')))

        # 个股新闻
        # ggxw = dataframe_to_records(await run_in_threadpool(lambda: ak.stock_news_em(symbol="002580")))

        # 集合镜像（不存在）
        # jjjj = dataframe_to_records(await run_in_threadpool(ak.stock_call_auction_em))

        # 美股
        # mgzd = dataframe_to_records(await run_in_threadpool(ak.stock_us_spot))
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
