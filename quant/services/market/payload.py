"""五模式 payload 构建（直调，不经 HTTP）。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi.concurrency import run_in_threadpool

from quant.data.tools.market import (
    async_holding_rows,
    fetch_concept_boards,
    fetch_hot,
    fetch_index_spot,
    fetch_industry_board,
    fetch_market_fund_flow,
    fetch_zqxy,
    fetch_ztgk,
)
from quant.services.market.enrich import enrich_optional_and_holding, enrich_optional_and_holding_from_rows
from quant.data.tools.payload_utils import finalize_quant_payload, merge_concept_boards, merge_industry_boards, theme_payload_stub
from quant.pool.candidate_config import (
    PAYLOAD_KEY_CXFL,
    PAYLOAD_KEY_CXG,
    PAYLOAD_KEY_LJQS,
    PAYLOAD_KEY_LXSZ,
    PAYLOAD_KEY_POPULARITY,
    PAYLOAD_KEY_ZT,
    THS_RANK_PAYLOAD_KEYS,
)
from quant.pool.candidate_sources import build_all_source_candidates
from quant.pool.ths_rank_util import (
    build_ths_rank_tag_map,
    enrich_list_with_ths_rank_tags,
    enrich_zt_stats_with_ths_rank,
)
from common.progress_log import log_progress, log_progress_done
from quant.services.market.intraday import IntradaySession, run_intraday_session


@dataclass
class BuildResult:
    payload: dict[str, Any]
    degraded: list[str] = field(default_factory=list)
    session: IntradaySession | None = None


async def build_news_payload(settings: Any) -> BuildResult:
    scope = "news"
    log_progress(scope, "开始聚合新闻")
    from quant.data.sources.interface import try_with_fallback
    news = try_with_fallback("info", "fetch_news")
    log_progress_done(scope, "新闻聚合完成", detail=f"共 {len(news)} 条")
    return BuildResult(payload=finalize_quant_payload(news))


async def build_pre_market_payload(settings: Any) -> BuildResult:
    scope = "pre_market"
    log_progress(scope, "开始构建盘前 payload")
    degraded: list[str] = []

    dpzs_ = await fetch_index_spot()
    if dpzs_ is None:
        degraded.append("大盘指数")

    zqxy_ = await fetch_zqxy(market_phase="intraday")
    if zqxy_ is None:
        degraded.append("赚钱效应")

    ztgk_ = await fetch_ztgk(settings)
    if not ztgk_:
        degraded.append("涨停概况")

    zxg_, ccg_ = await enrich_optional_and_holding(settings, progress_scope=scope)

    result = {
        "大盘指数": dpzs_,
        "赚钱效应": zqxy_,
        "涨停概况": ztgk_,
        "自选股": zxg_,
        "持仓股": ccg_,
    }
    if degraded:
        result["_degraded"] = degraded
    log_progress_done(scope, "盘前 payload 完成")
    return BuildResult(payload=finalize_quant_payload(result), degraded=degraded)


async def build_during_market_payload(settings: Any, *, session: IntradaySession | None = None) -> BuildResult:
    scope = "during_market"
    log_progress(scope, "开始构建盘中 payload")

    from quant.data.tools.market import async_holding_rows

    optional: list = []
    holding = await async_holding_rows()
    holding = holding if isinstance(holding, list) else []
    degraded: list[str] = []

    log_progress(scope, "并行拉取大盘/概念/行业四榜/涨停/人气 + enrich 自选/持仓")
    macro_task = asyncio.gather(
        fetch_index_spot(),
        fetch_zqxy(market_phase="intraday"),
        fetch_concept_boards(),
        fetch_industry_board("行业涨幅榜", "涨跌幅", True),
        fetch_industry_board("行业跌幅榜", "涨跌幅", False),
        fetch_industry_board("行业资金流入榜", "净流入", True),
        fetch_industry_board("行业资金流出榜", "净流入", False),
        fetch_ztgk(settings, True),
        fetch_hot(settings),
    )
    enrich_task = enrich_optional_and_holding_from_rows(
        settings,
        optional,
        holding,
        progress_scope=scope,
        include_pre_snapshot=True,
        skip_wencai=False,
        skip_jbxx=True,
    )
    macro, (zxg_, ccg_, _) = await asyncio.gather(macro_task, enrich_task)
    (
        dpzs,
        zqxy_,
        concept_boards,
        hy_gain,
        hy_loss,
        hy_fund,
        hy_fund_out,
        zttj,
        hot_,
    ) = macro

    if dpzs is None:
        degraded.append("大盘指数")
    if zqxy_ is None:
        degraded.append("赚钱效应")
    if concept_boards[0] is None:
        degraded.append("概念板块")
        jrzfqsgn = jrdfqsgn = jrzjlrqsgn = jrzjlcqsgn = None
    else:
        jrzfqsgn, jrdfqsgn, jrzjlrqsgn, jrzjlcqsgn = concept_boards

    gn_bk = merge_concept_boards(jrzfqsgn, jrzjlrqsgn, jrdfqsgn, jrzjlcqsgn)
    hy_bk = merge_industry_boards(hy_gain, hy_fund, hy_loss, hy_fund_out)

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy_,
        "概念板块": gn_bk,
        "行业板块": hy_bk,
        "涨停统计": zttj,
        PAYLOAD_KEY_POPULARITY: hot_,
        "自选股": zxg_,
        "持仓股": ccg_,
    }
    if degraded:
        result["_degraded"] = degraded
    if session is not None:
        result["_session"] = {
            "buy_block": session.buy_block,
            "sell_block": session.sell_block,
            "trades_executed": session.trades_executed,
            "trades_rejected": session.trades_rejected,
            "ok": session.ok,
            "error": session.error,
        }
    log_progress_done(scope, "盘中 payload 完成")
    return BuildResult(payload=finalize_quant_payload(result), degraded=degraded, session=session)


async def build_lunch_payload(settings: Any) -> BuildResult:
    scope = "post_market_lunch"
    log_progress(scope, "开始构建午间 payload")
    degraded: list[str] = []

    dpzs = await fetch_index_spot()
    if dpzs is None:
        degraded.append("大盘指数")
    zqxy_ = await fetch_zqxy(market_phase="intraday")
    if zqxy_ is None:
        degraded.append("赚钱效应")

    concept_boards, hy_gain, hy_loss, hy_fund, hy_fund_out = await asyncio.gather(
        fetch_concept_boards(),
        fetch_industry_board("行业涨幅榜", "涨跌幅", True),
        fetch_industry_board("行业跌幅榜", "涨跌幅", False),
        fetch_industry_board("行业资金流入榜", "净流入", True),
        fetch_industry_board("行业资金流出榜", "净流入", False),
    )
    if concept_boards[0] is None:
        degraded.append("概念板块")
        jrzfqsgn = jrdfqsgn = jrzjlrqsgn = jrzjlcqsgn = None
    else:
        jrzfqsgn, jrdfqsgn, jrzjlrqsgn, jrzjlcqsgn = concept_boards

    gn_bk = merge_concept_boards(jrzfqsgn, jrzjlrqsgn, jrdfqsgn, jrzjlcqsgn)
    hy_bk = merge_industry_boards(hy_gain, hy_fund, hy_loss, hy_fund_out)
    zttj = await fetch_ztgk(settings, True)
    zxg_, ccg_ = await enrich_optional_and_holding(settings, progress_scope=scope)

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy_,
        "概念板块": gn_bk,
        "行业板块": hy_bk,
        "涨停统计": zttj,
        "自选股": zxg_,
        "持仓股": ccg_,
    }
    if degraded:
        result["_degraded"] = degraded
    log_progress_done(scope, "午间 payload 完成")
    return BuildResult(payload=finalize_quant_payload(result), degraded=degraded)


async def build_evening_payload(settings: Any) -> BuildResult:
    scope = "post_market_evening"
    log_progress(scope, "开始构建晚间 payload")
    degraded: list[str] = []

    dpzs = await fetch_index_spot()
    if dpzs is None:
        degraded.append("大盘指数")
    zqxy_ = await fetch_zqxy(market_phase="closed")
    if zqxy_ is None:
        degraded.append("赚钱效应")
    zjl = await fetch_market_fund_flow(3)
    if zjl is None:
        degraded.append("大盘资金流")

    concept_boards, hy_gain, hy_loss, hy_fund, hy_fund_out = await asyncio.gather(
        fetch_concept_boards(),
        fetch_industry_board("行业涨幅榜", "涨跌幅", True),
        fetch_industry_board("行业跌幅榜", "涨跌幅", False),
        fetch_industry_board("行业资金流入榜", "净流入", True),
        fetch_industry_board("行业资金流出榜", "净流入", False),
    )
    if concept_boards[0] is None:
        degraded.append("概念板块")
        jrzfqsgn = jrdfqsgn = jrzjlrqsgn = jrzjlcqsgn = None
    else:
        jrzfqsgn, jrdfqsgn, jrzjlrqsgn, jrzjlcqsgn = concept_boards

    gn_bk = merge_concept_boards(jrzfqsgn, jrzjlrqsgn, jrdfqsgn, jrzjlcqsgn)
    hy_bk = merge_industry_boards(hy_gain, hy_fund, hy_loss, hy_fund_out)

    from quant.data.sources.interface import try_with_fallback
    zt_full = await run_in_threadpool(lambda: try_with_fallback("market", "fetch_ztgk_pool"))
    zttj = await fetch_ztgk(settings, True, zt_full=zt_full if isinstance(zt_full, list) else [])

    payload_stub = theme_payload_stub(gn_bk, hy_bk)
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
    ths_rows: list[dict] = []
    for key in THS_RANK_PAYLOAD_KEYS:
        ths_rows.extend(sources.get(key) or [])
    tag_map = build_ths_rank_tag_map(ths_rows)
    hot_ = enrich_list_with_ths_rank_tags(hot_, tag_map)
    zttj = enrich_zt_stats_with_ths_rank(zttj, tag_map)

    zxg_, ccg_, observe_ = await enrich_optional_and_holding(settings, progress_scope=scope)

    result = {
        "大盘指数": dpzs,
        "赚钱效应": zqxy_,
        "大盘资金流": zjl,
        "概念板块": gn_bk,
        "行业板块": hy_bk,
        "涨停统计": zttj,
        PAYLOAD_KEY_POPULARITY: hot_,
        PAYLOAD_KEY_ZT: zt_candidates,
        PAYLOAD_KEY_CXG: sources[PAYLOAD_KEY_CXG],
        PAYLOAD_KEY_LXSZ: sources[PAYLOAD_KEY_LXSZ],
        PAYLOAD_KEY_CXFL: sources[PAYLOAD_KEY_CXFL],
        PAYLOAD_KEY_LJQS: sources[PAYLOAD_KEY_LJQS],
        "自选股": zxg_,
        "持仓股": ccg_,
        "_observe_enriched": observe_,
    }
    if degraded:
        result["_degraded"] = degraded
    log_progress_done(scope, "晚间 payload 完成")
    return BuildResult(payload=finalize_quant_payload(result), degraded=degraded)


_BUILDERS = {
    "news": build_news_payload,
    "pre_market": build_pre_market_payload,
    "post_market_lunch": build_lunch_payload,
    "post_market_evening": build_evening_payload,
}


async def build_mode_payload_async(mode: str, settings: Any) -> BuildResult:
    if mode == "during_market":
        session = await run_in_threadpool(run_intraday_session)
        br = await build_during_market_payload(settings, session=session)
        br.session = session
        return br
    fn = _BUILDERS.get(mode)
    if fn is None:
        raise ValueError(f"未知模式: {mode}")
    return await fn(settings)


def build_mode_payload(mode: str, settings: Any) -> BuildResult:
    return asyncio.run(build_mode_payload_async(mode, settings))
