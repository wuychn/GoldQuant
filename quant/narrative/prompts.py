"""LLM 叙述 prompt。"""

from __future__ import annotations

import json
from datetime import datetime

from quant.config import LLM_OUTPUT_FORMAT
from quant.narrative.history_context import build_cross_day_context
from quant.store.state import get_total_assets, read_lessons, read_news_summary


def _persona() -> str:
    return (
        f"你是一名 A 股复盘文案编辑，当前总资产约 {get_total_assets():.0f} 元。"
        "你只负责把「研判要点」与接口数据整理成自然、可读的推送文案。\n"
        "禁止自行判定主线、龙头、买卖方向或加减仓；须与研判要点及文末「操作」段一致。\n"
        "研判要点为空时写「暂无」，勿从原始 JSON 自行推断。\n"
        "正文禁止出现「程序结论」「程序确认」「程序认定」「规则引擎」「程序归档」等系统用语。\n"
    )


def build_user_msg(
    payload: dict,
    *,
    mode: str = "",
    engine_brief: str = "",
    extra: str = "",
) -> str:
    news = read_news_summary()
    news_block = f"\n\n【当日新闻摘要】\n{news}\n" if news else ""
    lessons = read_lessons()
    lesson_block = f"\n\n【历史经验教训】\n{lessons[-1200:]}\n" if lessons else ""
    engine_block = f"\n\n{engine_brief}\n" if engine_brief else ""
    cross = build_cross_day_context(mode) if mode else ""
    cross_block = f"\n\n{cross}\n" if cross else ""
    body = json.dumps(payload, ensure_ascii=False)[:120000]
    return f"{news_block}{lesson_block}{engine_block}{cross_block}\n\n【接口数据 JSON】\n{body}\n{extra}"


def prompt_news() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return (
        f"当前日期：{today}。请对新闻去噪提炼。根据时间、重要性输出前50条要点和一段150字以内的综合解读，不要输出其他任何无关的内容。\n"
        + "以下是示例：\n"
        + "1. （新闻要点）\n"
        + "2. （新闻要点）\n"
        + "3. （新闻要点）\n"
        + ".....\n"
        + "50. （新闻要点）\n"
        + "\n"
        + "综合解读：（新闻解读）\n"
        + LLM_OUTPUT_FORMAT
    )


def _turnover_prompt_note(mode: str) -> str:
    if mode in ("during_market", "pre_market", "post_market_lunch"):
        return (
            "\n【成交额口径】叙述时引用 JSON 中 `赚钱效应.成交额`："
            "「今日累计」不可与「昨日全天」直接比较；放量/缩量以「较昨日同时段」为准。\n"
        )
    if mode == "post_market_evening":
        return (
            "\n【成交额口径】收盘后可将「今日全天/今日累计」与「昨日全天」对比；"
            "同时段变动参考「较昨日同时段」。\n"
        )
    return ""


def _data_semantics_note(mode: str) -> str:
    notes = [_turnover_prompt_note(mode)]
    notes.append(
        "\n【行情字段】上证涨跌幅见 `大盘指数`（代码000001）；涨跌家数/涨停跌停见 `赚钱效应`；"
        "最高连板见 `涨停统计.市场高度` 或涨停池连板数；勿虚构缺失字段。\n"
    )
    if mode in ("during_market", "pre_market", "post_market_lunch"):
        notes.append(
            "【字段口径】`大盘指数[].成交额` 为单指数累计（元），不可与 `赚钱效应.成交额` 全市场口径混比；"
            "`大盘指数[].量比` 为相对昨日同时段；`盘口.金额` 为个股当日累计；"
            "概念板块净额通常为亿元，个股资金流净额为万元。\n"
        )
        if mode == "pre_market":
            notes.append("【盘前提示】竞价前 `赚钱效应.成交额.今日累计` 可能为 0 或极小。\n")
    elif mode == "post_market_evening":
        notes.append(
            "【字段口径】`大盘指数[].成交额` 为指数收盘后累计；大盘资金流主力净流入单位为「元」；"
            "概念板块净额通常为亿元，个股资金流净额为万元。\n"
        )
    return "".join(n for n in notes if n)


_NARRATIVE_RULE = (
    "\n【叙述规则】"
    "「一、…主线…」等章节须与研判要点中的主线、龙头名单一致；"
    "可补充 JSON 中的指数/涨跌/成交等客观数据，但不得新增要点未列出的主线或龙头。"
    "勿输出买卖指令；操作结果以文末「操作/自选更新」段为准。"
    "全文用自然财经口吻，勿出现系统内部标签用语。\n"
)


def prompt_pre_market() -> str:
    return (
        _persona()
        + "撰写盘前一至三节纯叙述文案。\n"
        + _data_semantics_note("pre_market")
        + _NARRATIVE_RULE
        + "\n\n一、今日开盘概况\n二、自选股开盘分析\n三、持仓股开盘分析\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_during_market() -> str:
    return (
        _persona()
        + "撰写盘中一至四节纯叙述文案。\n"
        + _data_semantics_note("during_market")
        + _NARRATIVE_RULE
        + "\n\n一、市场概况\n二、主线与概念\n三、自选股表现\n四、持仓监控\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_lunch_review() -> str:
    return (
        _persona()
        + "撰写午间复盘一至五节纯叙述文案，不要输出自选更新。\n"
        + _data_semantics_note("post_market_lunch")
        + _NARRATIVE_RULE
        + "\n\n一、上午大盘\n二、主线变化\n三、自选股表现\n四、持仓跟踪\n五、下午策略\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_evening_review() -> str:
    return (
        _persona()
        + "撰写晚间复盘一至八节纯叙述文案，不要输出自选更新。\n"
        + _data_semantics_note("post_market_evening")
        + _NARRATIVE_RULE
        + "\n\n一、全天大盘\n二、主线复盘\n三、自选股表现\n四、持仓复盘\n"
        + "五、盈亏总结\n六、经验总结\n七、明日展望\n八、风险提示\n"
        + LLM_OUTPUT_FORMAT
    )
