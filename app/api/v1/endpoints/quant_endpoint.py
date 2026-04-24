"""openclaw量化数据入口"""

from __future__ import annotations

import akshare as ak
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from app.api.deps import SettingsDep
from app.schemas.response import Response
from app.utils.common_util import list_to_dict, call_ths_api, format_percent, today
from app.utils.dataframe import dataframe_to_records

router = APIRouter(tags=["量化入口"])


@router.get(
    "/quant/market/review",
    response_model=Response,
    summary="复盘",
    description="复盘",
)
async def market_review() -> Response:
    # 指数历史K线（有问题）
    zslskx = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.index_zh_a_hist(symbol="000001"))))
    # 资金流，只获取最近3天（非实时？）
    fund = dataframe_to_records(await run_in_threadpool(ak.stock_market_fund_flow))[-3:]
    # 涨跌情况（近实时）
    dpzd = list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_market_activity_legu)))
    # 涨停家数
    ztgc = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.stock_zt_pool_em(date=today()))))

    result = {
        "指数历史K线": zslskx,
        "近三个交易日大盘资金流入情况": fund,
        "当日大盘涨跌情况": dpzd,
        "涨停股": ztgc
    }
    return Response(data=result)


@router.get(
    "/quant/market/overview",
    response_model=Response,
    summary="大盘概览",
    description="大盘概览，包括资金流、涨跌数等",
)
async def market_overview() -> Response:
    try:
        # 资金流，只获取最近3天（非实时？）
        fund = dataframe_to_records(await run_in_threadpool(ak.stock_market_fund_flow))[-3:]
        # 涨跌情况（近实时）
        dpzd = list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_market_activity_legu)))
        # 东方财富大盘指数（实时）（经常出问题，33.push2域名怎么绕开？）
        dpzs = [item for item in dataframe_to_records(
            await run_in_threadpool(lambda: ak.stock_zh_index_spot_em(symbol="上证系列指数"))) if
                item["序号"] in [1, 2, 4]]
        # 指数历史K线（有问题）
        # zslskx = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.index_zh_a_hist(symbol="000001"))))
        result = {
            "近三个交易日大盘资金流入情况": fund,
            "当日大盘涨跌情况": dpzd,
            # "大盘指数": dpzs
        }



        # 赚钱效应（没有这个方法）
        # zslskx = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.stock_a_all_em())))

        # 创业板情况（方法不存在）
        # zslskx = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.stock_zh_cy_spot)))
        # zslskx = list_to_dict(dataframe_to_records(await run_in_threadpool(lambda: ak.stock_zh_cy_daily)))

        # 科创板情况（科创板实时 / 历史）
        # kcbss = list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_zh_kcb_spot)))
        # kcbls = list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_zh_kcb_daily)))

        # 行业涨幅/概念涨幅/行业资金流入排名
        # list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_board_industry_em)))
        # list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_board_concept_em)))
        # hyzjlrpm = list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_sector_fund_flow_rank)))
        # list_to_dict(dataframe_to_records(await run_in_threadpool(ak.stock_board_industry_spot)))

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

        # result = {
        #     "资金流": fund,
        #     "涨跌情况": zd,
        #     "沪深重要指数": hszyzs
        # }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(data=result)


@router.get(
    "/quant/stock/hot",
    response_model=Response,
    summary="同花顺人气股",
    description=(
            "同花顺人气股"
    ),
)
async def stock_hot(
        settings: SettingsDep,
) -> Response:
    r = await call_ths_api(
        settings,
        "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal"
    )
    result = []
    for s in r['data']['stock_list']:
        code_ = s['code']
        if code_.startswith('60') or code_.startswith('0') or code_.startswith('3'):
            result.append({
                '市场': '深证' if s['market'] == 33 else '上证',
                '股票代码': s['code'],
                '股票名称': s['name'],
                '热度': str(float(s['rate'])).replace(".0", ''),
                '涨跌': format_percent(s['rise_and_fall']),
                '人气排名': s['order'],
                '人气排名变化': f'上升{s["hot_rank_chg"]}位' if s['hot_rank_chg'] > 0 else '无变化' if s[
                                                                                                           'hot_rank_chg'] == 0 else f'下降{s["hot_rank_chg"]}位',
                '概念': s['tag']['concept_tag'] if 'tag' in s and 'concept_tag' in s['tag'] else '无',
                '连扳情况': s['tag']['popularity_tag'] if 'tag' in s and 'popularity_tag' in s['tag'] else '无'
            })
    return Response(
        data=result
    )


@router.get(
    "/quant/stock/skyrocket",
    response_model=Response,
    summary="同花顺人气飙升榜",
    description=(
            "同花顺人气飙升榜"
    ),
)
async def stock_skyrocket(
        settings: SettingsDep,
) -> Response:
    r = await call_ths_api(
        settings,
        "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=skyrocket"
    )
    result = []
    for s in r['data']['stock_list']:
        code_ = s['code']
        if code_.startswith('60') or code_.startswith('0') or code_.startswith('3'):
            result.append({
                '市场': '深证' if s['market'] == 33 else '上证',
                '股票代码': s['code'],
                '股票名称': s['name'],
                '热度': str(float(s['rate'])).replace(".0", ''),
                '涨跌': format_percent(s['rise_and_fall']),
                '人气排名': s['order'],
                '人气排名变化': f'上升{s["hot_rank_chg"]}位' if s['hot_rank_chg'] > 0 else '无变化' if s[
                                                                                                           'hot_rank_chg'] == 0 else f'下降{s["hot_rank_chg"]}位',
                '概念': s['tag']['concept_tag'] if 'tag' in s and 'concept_tag' in s['tag'] else '无',
                '连扳情况': s['tag']['popularity_tag'] if 'tag' in s and 'popularity_tag' in s['tag'] else '无'
            })
    return Response(
        data=result
    )
