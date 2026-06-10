"""LLM 叙述 prompt。"""

from __future__ import annotations

import json
from datetime import datetime

from quant.config import LLM_OUTPUT_FORMAT
from quant.narrative.history_context import build_cross_day_context
from quant.narrative.push_style import DEPRECATED_PUSH_TERMS
from quant.store.state import get_total_assets, read_lessons, read_news_summary


def _push_vocabulary_note() -> str:
    banned = "、".join(
        t for t in DEPRECATED_PUSH_TERMS if t not in ("可参与交易",)
    )
    return (
        "行情强弱统一写「赚钱效应强/一般/差」，仓位上限写「仓位控制」及具体比例；"
        f"禁止「{banned}」及单独使用「主线」「龙头」等旧表述。\n"
    )


def _persona() -> str:
    return (
        f"你是一名 A 股复盘文案编辑，当前总资产约 {get_total_assets():.0f} 元。"
        "你只负责把写作参考块与接口数据整理成自然、可读的飞书推送正文。\n"
        "禁止自行判定买卖方向或加减仓；须与写作参考及文末「操作」段一致。\n"
        "参考信息为空时写「暂无」，勿从原始 JSON 自行推断。\n"
        "当前持仓以写作参考「当前持仓」及 JSON「持仓股」为准；"
        "有持仓时禁止写「空仓」「无持仓」「未持股」。\n"
        "正文须符合人类阅读习惯：直接写「当日涨幅」「资金流入」「概念板块」「主升波段」等，"
        "禁止「研判中的当日涨幅」「研判要点」「写作参考」「接口数据 JSON」等内部用语。\n"
        "正文禁止出现「程序结论」「程序确认」「程序认定」「规则引擎」「程序归档」"
        "「全局门禁」「门禁」「标的池」等系统用语。\n"
        + _push_vocabulary_note()
    )


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
        notes.append(
            "【晚间专节】二、自选股表现：仅写写作参考「自选股表现范围」内个股，"
            "勿纳入「本轮新入选」。"
            "三、操作复盘：依据「今日操作」与 JSON「持仓股」，复盘当日买卖、持股、做T 等；"
            "无成交须写「今日无买卖操作」，勿虚构操作。"
            "四、盈亏总结：严格依据「当日盈亏参考」；无成交且浮动有限时写基本持平，"
            "禁止写「大幅盈利/亏损」等与数据不符的表述。\n"
        )
    return "".join(n for n in notes if n)


_NARRATIVE_RULE_HEAD = (
    "\n【叙述规则】"
    "章节标题用「一、大盘概况」等自然小节名；"
    "描述行情强弱用「赚钱效应强/一般/差」，描述仓位用「仓位控制」及具体比例。"
    "概念板块、资金动向须与写作参考一致，可补充指数/涨跌/成交等客观数据，"
    "但不得新增参考中未列出的概念名称。"
    "禁止在正文出现「主线」「龙头」「主线龙头」「主线复盘」等用语。"
)

_NARRATIVE_RULE_TAIL = (
    "全文用口语化财经复盘口吻，字段名直接写「当日涨幅」「资金流入」，"
    "勿加「研判」「要点」「参考」「程序」等前缀，勿照抄【】标签行。\n"
)


def _narrative_rule(*, ops_note: str) -> str:
    return _NARRATIVE_RULE_HEAD + ops_note + _NARRATIVE_RULE_TAIL


def prompt_pre_market() -> str:
    return (
        _persona()
        + "撰写盘前一至三节纯叙述文案。\n"
        + _data_semantics_note("pre_market")
        + _narrative_rule(ops_note="勿输出买卖指令；操作结果以文末「操作」段为准。")
        + "\n\n一、大盘概况\n二、自选股开盘分析\n三、持仓股开盘分析\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_during_market() -> str:
    return (
        _persona()
        + "撰写盘中一至三节纯叙述文案；盘中仅对已入自选个股按策略买卖，"
        "不新增或删除自选股，正文勿出现「自选更新」。\n"
        + _data_semantics_note("during_market")
        + _narrative_rule(
            ops_note=(
                "勿输出买卖指令或自选变更；操作结果以文末「操作」段为准。"
                "大盘概况须一并交代指数、涨跌家数、成交、概念板块与资金动向，"
                "勿另设独立「概念复盘」小节。"
            )
        )
        + "\n\n一、大盘概况\n二、自选股表现\n三、持仓监控\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_lunch_review() -> str:
    return (
        _persona()
        + "撰写午间复盘一至四节纯叙述文案，不要输出自选更新。\n"
        + _data_semantics_note("post_market_lunch")
        + _narrative_rule(ops_note="勿输出买卖指令或自选变更；不要输出自选更新小节。")
        + "\n\n一、大盘概况\n二、自选股表现\n三、持仓跟踪\n四、下午策略\n"
        + LLM_OUTPUT_FORMAT
    )


def prompt_evening_review() -> str:
    return (
        _persona()
        + "撰写晚间复盘一至六节纯叙述文案，不要输出自选更新。\n"
        + _data_semantics_note("post_market_evening")
        + _narrative_rule(
            ops_note=(
                "勿输出买卖指令或自选变更；自选结果以文末「自选更新」段为准，正文不要输出自选更新小节。"
                "大盘概况须一并交代指数、涨跌家数、成交、概念板块与资金动向。"
            )
        )
        + "\n\n一、大盘概况\n二、自选股表现\n三、操作复盘\n"
        + "四、盈亏总结\n五、经验总结\n六、明日展望与风险提示\n"
        + LLM_OUTPUT_FORMAT
    )
