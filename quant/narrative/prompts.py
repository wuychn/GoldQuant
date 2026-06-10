"""LLM 叙述 prompt 入口（各模式组装见 prompt_common）。"""

from __future__ import annotations

import json
from datetime import datetime

from quant.config import LLM_OUTPUT_FORMAT
from quant.narrative.history_context import build_cross_day_context
from quant.narrative.prompt_common import compose_review_prompt
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
        f"当前日期：{today}。请对新闻去噪提炼。根据时间、重要性输出前50条要点和一段150字以内的综合解读，不要输出其他任何无关的内容。\n"
        + "以下是示例：\n"
        + "1. （要点）\n"
        + "2. （要点）\n"
        + "3. （要点）\n"
        + ".....\n"
        + "50. （新闻要点）\n"
        + "\n"
        + "综合解读：（新闻解读）\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_pre_market() -> str:
    return compose_review_prompt(
        task="撰写盘前一至三节纯叙述文案。\n",
        mode="pre_market",
        ops_note="勿输出买卖指令；操作结果以文末「操作」段为准。",
        sections="一、大盘概况\n二、自选股开盘分析\n三、持仓股开盘分析",
    )


def prompt_during_market() -> str:
    return compose_review_prompt(
        task=(
            "撰写盘中一至三节纯叙述文案；盘中仅对已入自选个股按策略买卖，"
            "不新增或删除自选股，正文勿出现「自选更新」。\n"
        ),
        mode="during_market",
        ops_note=(
            "勿输出买卖指令或自选变更；买卖结果仅由文末「四、操作」段给出，正文一至三节禁止出现"
            "「操作」「买卖」「加仓」「减仓」「无买卖」等交易动作表述。"
            "三、持仓监控只写持股走势、盈亏体感与风险关注，不写是否下单。"
            "大盘概况须一并交代指数、涨跌家数、成交、概念板块与资金动向，"
            "勿另设独立「概念复盘」小节。"
        ),
        sections="一、大盘概况\n二、自选股表现\n三、持仓监控",
    )


def prompt_lunch_review() -> str:
    return compose_review_prompt(
        task="撰写午间复盘一至四节纯叙述文案，不要输出自选更新。\n",
        mode="post_market_lunch",
        ops_note=(
            "勿输出买卖指令或自选变更；一至四节禁止出现「操作」「买卖」「加仓」「减仓」"
            "「无买卖」「今日无买卖操作」等交易动作表述。"
            "四、下午策略只写午后节奏与关注方向，不写是否下单。"
        ),
        sections="一、大盘概况\n二、自选股表现\n三、持仓股表现\n四、下午策略",
    )


def prompt_evening_review() -> str:
    return compose_review_prompt(
        task="撰写晚间复盘一至七节纯叙述文案，不要输出自选更新。\n",
        mode="post_market_evening",
        ops_note=(
            "勿输出买卖指令或自选变更；自选结果以文末「八、自选更新」段为准，正文不要输出自选更新小节。"
            "一至三、五至七节禁止写买卖动作；买卖复盘仅出现在「四、操作复盘」。"
            "大盘概况须一并交代指数、涨跌家数、成交、概念板块与资金动向。"
        ),
        sections=(
            "一、大盘概况\n二、自选股表现\n三、持仓股表现\n四、操作复盘\n"
            "五、盈亏总结\n六、经验总结\n七、明日展望与风险提示"
        ),
    )
