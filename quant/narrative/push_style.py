"""飞书推送与自然叙述共用的文案约定。"""

from __future__ import annotations

# 注入 LLM 的 engine_brief 分隔行（勿使用会在正文出现的「研判」等字样）
BRIEF_PREAMBLE = (
    "（以下为写作参考，勿将本节标题及【】标签原样写入推送正文；"
    "正文用自然财经表述，如「赚钱效应强/一般/差」「仓位控制」「当日涨幅」「资金流入」「概念板块」；"
    "禁止出现「主线」「龙头」等表述）"
)

# infer_regime 内部档位 → 推送自然表述
PROFIT_EFFECT_LEVELS = {
    "强势": "强",
    "震荡": "一般",
    "弱势": "差",
}

# 推送正文禁用旧表述（供 prompt / sanitize 共用）
DEPRECATED_PUSH_TERMS = (
    "市场环境",
    "市场档位",
    "市场状态",
    "交易环境",
    "可参与交易",
    "主线龙头",
    "确认主线",
    "涨幅主线",
    "资金主线",
    "主线复盘",
    "主线与概念",
    "主线变化",
)


def profit_effect_level(payload: dict | None) -> str:
    """由 payload 推断赚钱效应强弱（强/一般/差）。"""
    from quant.scoring.context import infer_regime

    regime = infer_regime(payload) if payload else "震荡"
    return PROFIT_EFFECT_LEVELS.get(regime, "一般")


# 自选股（候选）与持仓股（已买入）分轨表述，禁止混写或互相替代
WATCHLIST_HOLDINGS_SEPARATION = (
    "「自选股」为候选池，「持仓股」为已买入仓位；"
    "谈买入、买点、接回仅针对自选股，谈卖出、止损、持股仅针对持仓股，禁止混写或互相替代。\n"
)

PUSH_COLLOQUIAL_TONE = (
    "语气像与股友聊盘面：短句、有判断，多用盘面、题材、资金、节奏、承接、分化、"
    "轮动、强弱、筹码等市场用语；不堆砌系统规则、阈值数字或内部字段名。\n"
)

NARRATIVE_RULE_HEAD = (
    "\n【叙述规则】"
    "章节标题用「一、大盘概况」等自然小节名；"
    "描述行情强弱用「赚钱效应强/一般/差」，描述仓位用「仓位控制」及具体比例。"
    "概念板块、资金动向须与写作参考一致，可补充指数/涨跌/成交等客观数据，"
    "但不得新增参考中未列出的概念名称。"
    "禁止在正文出现「主线」「龙头」「主线龙头」「主线复盘」等用语。"
)

NARRATIVE_RULE_CLOSING = (
    "字段名可直接写「当日涨幅」「资金流入」，"
    "勿加「研判」「要点」「参考」「程序」等前缀，勿照抄【】标签行。\n"
)


def compose_narrative_rule(*, ops_note: str) -> str:
    """组装叙述规则块：专节约束 + 共用口语化口吻。"""
    return NARRATIVE_RULE_HEAD + ops_note + PUSH_COLLOQUIAL_TONE + NARRATIVE_RULE_CLOSING


# 飞书正文清洗：允许出现买卖表述的章节标题（从早到晚取最前边界）
PUSH_OPS_SECTION_MARKERS = (
    "八、自选更新",
    "七、自选更新",
    "四、操作复盘",
    "四、操作",
)

# 旧表述 → 推送自然表述（sanitize 与 prompt 共用）
DEPRECATED_TERM_REPLACEMENTS: dict[str, str] = {
    "市场环境": "赚钱效应",
    "市场档位": "赚钱效应",
    "市场状态": "赚钱效应",
    "交易环境": "仓位控制",
    "主线龙头": "主升波段",
    "确认主线": "概念板块",
    "涨幅主线": "涨幅靠前概念",
    "资金主线": "资金流入概念",
    "主线复盘": "大盘概况",
    "主线与概念": "概念板块",
    "主线变化": "概念板块",
}
