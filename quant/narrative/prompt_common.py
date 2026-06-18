"""LLM 叙述 prompt 公共块：角色、字段口径、叙述规则组装。"""

from __future__ import annotations

from quant.config import LLM_OUTPUT_FORMAT
from quant.narrative.push_style import (
    DEPRECATED_PUSH_TERMS,
    WATCHLIST_HOLDINGS_SEPARATION,
    compose_narrative_rule,
)
from quant.store.state import get_total_assets

# 盘中/盘前/午间共用成交额与字段口径
_INTRADAY_SEMANTICS_MODES = frozenset({"during_market", "pre_market", "post_market_lunch"})


def push_vocabulary_note() -> str:
    banned = "、".join(t for t in DEPRECATED_PUSH_TERMS if t not in ("可参与交易",))
    return (
        "行情强弱统一写「赚钱效应强/一般/差」，仓位上限写「仓位控制」及具体比例；"
        f"禁止「{banned}」及单独使用「主线」「龙头」等旧表述。\n"
    )


def build_persona() -> str:
    """A 股复盘文案编辑角色与通用禁令。"""
    return (
        f"你是一名 A 股复盘文案编辑，当前总资产约 {get_total_assets():.0f} 元。"
        "你只负责把写作参考块与接口数据整理成自然、可读的飞书推送正文。\n"
        "禁止自行判定买卖方向或加减仓；须与写作参考及文末「操作」段一致。\n"
        "参考信息为空时写「暂无」，勿从原始 JSON 自行推断。\n"
        "当前持仓以写作参考「当前持仓」及 JSON「持仓股」为准；"
        "有持仓时禁止写「空仓」「无持仓」「未持股」。\n"
        "自选股见 JSON「自选股」或写作参考「自选股表现范围」，与持仓股分轨叙述，禁止混为一谈。\n"
        "正文须符合人类阅读习惯：直接写「当日涨幅」「资金流入」「概念板块」「主升波段」等，"
        "禁止「研判中的当日涨幅」「研判要点」「写作参考」「接口数据 JSON」等内部用语。\n"
        "正文禁止出现「程序结论」「程序确认」「程序认定」「规则引擎」「程序归档」"
        "「全局门禁」「门禁」「标的池」等系统用语。\n"
        "禁止照抄写作参考中的标签行（如「当日涨幅概念：」「资金流入行业：」「主升波段（N只）：」等）。\n"
        + push_vocabulary_note()
    )


def _turnover_prompt_note(mode: str) -> str:
    if mode in _INTRADAY_SEMANTICS_MODES:
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


def data_semantics_note(mode: str) -> str:
    """各模式字段口径与专节约束。"""
    notes = [_turnover_prompt_note(mode)]
    notes.append(
        "\n【行情字段】上证涨跌幅见 `大盘指数`（代码000001）；涨跌家数/涨停跌停见 `赚钱效应`；"
        "最高连板见 `涨停统计.市场高度` 或涨停池连板数；勿虚构缺失字段。\n"
    )
    if mode in _INTRADAY_SEMANTICS_MODES:
        notes.append(
            "【字段口径】`大盘指数[].成交额` 为单指数累计（元），不可与 `赚钱效应.成交额` 全市场口径混比；"
            "`大盘指数[].量比` 为相对昨日同时段；`盘口.金额` 为个股当日累计；"
            "概念板块净额通常为亿元，个股资金流净额为万元。\n"
        )
        if mode == "pre_market":
            notes.append(
            "【盘前专节】竞价前 `赚钱效应.成交额.今日累计` 可能为 0 或极小；"
            "盘前无概念/行业榜单，第一节勿写「当日涨幅概念/行业暂无」类占位。"
            "第二节写自选股竞价/开盘关注点，第三节写持仓股开盘关注点，分轨叙述。\n"
            )
        if mode == "during_market":
            notes.append(
                "【盘中时刻】A股9:30连续竞价开盘；正文时间表述须与推送标题时刻一致。"
                "9:37约开盘7分钟、9:47约17分钟，10:00才约半小时。"
                "10:00前禁止写「开盘半小时」「半小时行情」「开盘已半小时」；"
                "可写「早盘」「开盘不久」「上午盘中」等。\n"
            )
            notes.append(
                "【智能盯盘】第二节：JSON「自选股」；第三节：JSON「持仓股」及写作参考「持仓股表现」。"
                "两节分轨叙述，禁止混写。"
                "正文须写出当日盈亏与账户（可用、持仓市值、总资产），严格依据写作参考「当日盈亏与账户」。\n"
            )
    elif mode == "post_market_evening":
        notes.append(
            "【字段口径】`大盘指数[].成交额` 为指数收盘后累计；大盘资金流主力净流入单位为「元」；"
            "概念板块净额通常为亿元，个股资金流净额为万元。\n"
        )
        notes.append(
            "【晚间专节】"
            "二、自选股表现：仅写写作参考「自选股表现范围」，不含本轮新入选。"
            "三、持仓股表现：依据写作参考「持仓股表现」与 JSON「持仓股」，写持股涨跌与强弱，不写买卖。"
            "四、操作复盘：依据「今日操作」与 JSON「持仓股」，复盘当日买卖、持股、做T；"
            "无成交写「今日无买卖操作」，勿虚构。"
            "五、总结与展望：须在同一节内依次写清——"
            "① 当日盈亏与账户（可用、持仓市值、总资产），严格依据写作参考「当日盈亏与账户」，"
            "直接写当日盈亏一个数字，禁止拆分浮盈/浮亏表述；"
            "② 今日操作与盘面经验（可借鉴历史经验教训，勿抄标题）；"
            "③ 次日关注方向与主要风险。"
            "三段连贯叙述，禁止再设「六、经验总结」「七、明日展望」等一级标题。\n"
        )
    if mode == "post_market_lunch":
        notes.append(
            "【午间专节】二、自选股表现：依据 JSON「自选股」与写作参考「自选股表现范围」。"
            "三、持仓股表现：依据写作参考「持仓股表现」。"
            "四、午后策略：只写午后节奏、题材延续与关注点，禁止写买卖类表述。"
            "正文须写出当日盈亏与账户（可用、持仓市值、总资产），严格依据写作参考「当日盈亏与账户」。\n"
        )
    return "".join(n for n in notes if n)


def compose_review_prompt(
    *,
    task: str,
    mode: str,
    ops_note: str,
    sections: str,
) -> str:
    """复盘类 prompt：角色 + 任务 + 字段口径 + 叙述规则 + 章节大纲。"""
    body = (
        build_persona()
        + WATCHLIST_HOLDINGS_SEPARATION
        + task
        + data_semantics_note(mode)
        + compose_narrative_rule(ops_note=ops_note)
        + f"\n\n{sections.strip()}\n"
        + LLM_OUTPUT_FORMAT
    )
    return body
