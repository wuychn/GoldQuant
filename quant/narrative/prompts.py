"""LLM 叙述 prompt。"""

from __future__ import annotations

import json
from datetime import datetime

from quant.config import LLM_OUTPUT_FORMAT
from quant.narrative.strategy import load_sections
from quant.store.state import get_total_assets, read_lessons, read_news_summary


def _persona() -> str:
    return (
        f"你是一名 A 股实盘短线高手，当前总资产约 {get_total_assets():.0f} 元。"
        "你只做数据解读与复盘叙述，不做买卖决策。\n"
    )


def build_user_msg(payload: dict, *, extra: str = "") -> str:
    news = read_news_summary()
    news_block = f"\n\n【当日新闻摘要】\n{news}\n" if news else ""
    lessons = read_lessons()
    lesson_block = f"\n\n【历史经验教训】\n{lessons[-1200:]}\n" if lessons else ""
    body = json.dumps(payload, ensure_ascii=False)[:120000]
    return f"{news_block}{lesson_block}\n\n【接口数据 JSON】\n{body}\n{extra}"


def prompt_news() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return (
        _persona()
        + f"当前日期：{today}。请对新闻去噪提炼。\n"
        + "输出前50条要点 + 一段150字以内的综合解读。\n"
        + LLM_OUTPUT_FORMAT
    )


def _turnover_prompt_note(mode: str) -> str:
    if mode in ("during_market", "pre_market", "post_market_lunch"):
        return (
            "\n【成交额口径】引用 `赚钱效应.成交额`："
            "「今日累计」仅截至采集时刻，不可与「昨日全天」直接比较；"
            "判断放量/缩量以「较昨日同时段」为准；勿编造全天对比结论。\n"
        )
    if mode == "post_market_evening":
        return (
            "\n【成交额口径】引用 `赚钱效应.成交额`："
            "收盘后可将「今日全天/今日累计」与「昨日全天」对比；"
            "同时段变动参考「较昨日同时段」。\n"
        )
    return ""


def _data_semantics_note(mode: str) -> str:
    notes = [_turnover_prompt_note(mode)]
    if mode in ("during_market", "pre_market", "post_market_lunch"):
        notes.append(
            "\n【其他字段口径】"
            "大盘指数[].成交额 为单指数当日累计（元），不可与 赚钱效应.成交额 全市场口径混比；"
            "大盘指数[].量比 为相对昨日同时段；"
            "盘口.金额 为个股当日累计成交额（元），历史行情[].成交额 为历史全日；"
            "概念板块.*.净额 为行业资金流（通常亿元级），个股资金流.净额 为万元。\n"
        )
        if mode == "pre_market":
            notes.append(
                "【盘前提示】竞价前 ``赚钱效应.成交额.今日累计`` 可能为 0 或极小，勿强行解读量能。\n"
            )
    elif mode == "post_market_evening":
        notes.append(
            "\n【其他字段口径】"
            "大盘指数[].成交额 为指数收盘后累计；大盘资金流 主力净流入单位为「元」；"
            "概念板块.*.净额 通常为亿元，个股资金流.净额 为万元。\n"
        )
    return "".join(n for n in notes if n)


def prompt_pre_market() -> str:
    return (
        _persona()
        + "撰写盘前一至三节，不要输出操作指令。策略仅为「主升浪战法」：主线龙头、均线发散向上。\n"
        + load_sections("共用约束", "市场状态机", "仓位联动", "主升浪战法")
        + _data_semantics_note("pre_market")
        + "\n\n一、今日开盘概况\n二、自选股开盘分析\n三、持仓股开盘分析\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_during_market() -> str:
    return (
        _persona()
        + "撰写盘中一至四节，不要输出操作指令。仅主升浪战法；买卖需三确认，勿在叙述中直接下单。\n"
        + load_sections("共用约束", "市场状态机", "主升浪战法")
        + _data_semantics_note("during_market")
        + "\n\n一、市场概况\n二、主线与概念\n三、自选股表现\n四、持仓监控\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_lunch_review() -> str:
    return (
        _persona()
        + "撰写午间复盘一至五节，不要输出自选更新。\n"
        + load_sections("共用约束", "市场状态机", "仓位联动")
        + _data_semantics_note("post_market_lunch")
        + "\n\n一、上午大盘\n二、主线变化\n三、自选股表现\n四、持仓跟踪\n五、下午策略\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_evening_review() -> str:
    return (
        _persona()
        + "撰写晚间复盘一至八节，不要输出自选更新。\n"
        + load_sections("共用约束", "市场状态机", "仓位联动", "每日亏损限额")
        + _data_semantics_note("post_market_evening")
        + "\n\n一、全天大盘\n二、主线复盘\n三、自选股表现\n四、持仓复盘\n"
        + "五、盈亏总结\n六、经验总结\n七、明日展望\n八、风险提示\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_no_trade_reason(mode: str) -> str:
    scene = "盘前" if mode == "pre_market" else "盘中"
    return (
        f"你是A股交易助手。根据材料用一句话说明{scene}为何无成交，30~120字，以「原因：」开头。"
        "不要编造材料中没有的信息。"
    )
