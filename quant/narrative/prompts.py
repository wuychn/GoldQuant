"""LLM 叙述 prompt 入口（各模式组装见 prompt_common）。"""

from __future__ import annotations

import json
from datetime import datetime

from quant.config import LLM_OUTPUT_FORMAT
from quant.narrative.history_context import build_cross_day_context
from quant.narrative.prompt_common import compose_review_prompt
from quant.narrative.stock_lines import WATCHLIST_SECTION_TITLE
from quant.store.state import read_lessons, read_news_summary


def build_user_msg(
    payload: dict,
    *,
    mode: str = "",
    engine_brief: str = "",
    extra: str = "",
) -> str:
    news = read_news_summary()
    news_block = f"\n\n当日新闻摘要（勿抄标题）：\n{news}\n" if news else ""
    lessons = read_lessons()
    lesson_block = f"\n\n历史经验教训（勿抄标题）：\n{lessons[-1200:]}\n" if lessons else ""
    engine_block = f"\n\n{engine_brief}\n" if engine_brief else ""
    cross = build_cross_day_context(mode) if mode else ""
    cross_block = f"\n\n{cross}\n" if cross else ""
    body = json.dumps(payload, ensure_ascii=False)[:120000]
    return (
        f"{news_block}{lesson_block}{engine_block}{cross_block}\n\n"
        f"行情数据（供核对，勿在正文提及 JSON 或字段路径）：\n{body}\n{extra}"
    )


def prompt_news() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return (
        f"当前日期：{today}。请对新闻去噪提炼。按时间、重要性输出前30条要点，"
        f"每条一行，格式为「序号. 要点正文」，要点正文不要用括号包裹，不要加引号。\n"
        f"最后单独一段「综合解读：」150字以内。\n"
        f"示例：\n"
        f"1. 沪指收涨0.3%，成交额2.1万亿\n"
        f"2. 商务部发布消费品以旧换新最新数据\n"
        f"……\n"
        f"综合解读：今日市场……\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_pre_market() -> str:
    return compose_review_prompt(
        task=(
            "撰写盘前三节推送正文：聚焦竞价前/开盘初的市场环境与个股准备，"
            "不下单、不涉及自选变更。\n"
        ),
        mode="pre_market",
        ops_note=(
            "禁止输出买卖、加减仓及「操作」类表述。"
            "第二节仅写自选股，第三节仅写持仓股，不得混写。"
            "竞价前成交额可能极低，勿夸大量能；无数据处写「暂无」。"
        ),
        sections="一、大盘概况\n二、自选股开盘分析\n三、持仓股开盘分析",
    )


def prompt_during_market() -> str:
    return compose_review_prompt(
        task=(
            "撰写盘中一至三节推送正文：概括当前盘面、自选股与持仓股表现。"
            "盘中仅对已入自选个股按策略交易，不调整自选列表。\n"
        ),
        mode="during_market",
        ops_note=(
            "禁止在正文一至三节出现「操作」「买卖」「加仓」「减仓」「无买卖」等交易表述；"
            "买卖结果仅由程序在文末「四、操作」段给出。"
            "第二节仅写 JSON「自选股」，第三节仅写 JSON「持仓股」，不得混写。"
            "第一节须交代指数、涨跌家数、成交（注意同时段口径）、概念与资金动向，"
            "不另设「概念复盘」小节。"
        ),
        sections="一、大盘概况\n二、自选股表现\n三、持仓股表现",
    )


def prompt_lunch_review() -> str:
    return compose_review_prompt(
        task=(
            "撰写午间复盘一至四节推送正文：总结上午盘面与持仓，"
            "给出午后关注方向；不涉及买卖与自选变更。\n"
        ),
        mode="post_market_lunch",
        ops_note=(
            "禁止输出买卖、加减仓及「操作」类表述。"
            "第二节写自选股上午表现，第三节写持仓股上午表现，第四节写午后策略与风险点，"
            "第四节只谈节奏与关注点，不写是否下单。"
        ),
        sections="一、大盘概况\n二、自选股表现\n三、持仓股表现\n四、午后策略",
    )


def prompt_evening_review() -> str:
    wl_title = WATCHLIST_SECTION_TITLE
    return compose_review_prompt(
        task=(
            "撰写晚间复盘一至五节推送正文：全天复盘与收束，"
            "不输出自选变更（自选结果由程序追加）。\n"
        ),
        mode="post_market_evening",
        ops_note=(
            f"禁止在正文输出自选变更；自选结果以文末「{wl_title}」为准。"
            "第一至三节、第五节禁止写买卖动作；买卖复盘仅出现在「四、操作复盘」。"
            "第一节须交代指数、涨跌家数、全天成交、概念与资金动向。"
            "第五节须合并盈亏、经验与次日展望，勿再拆为多个一级小节。"
        ),
        sections=(
            "一、大盘概况\n二、自选股表现\n三、持仓股表现\n四、操作复盘\n"
            "五、总结与展望"
        ),
    )
