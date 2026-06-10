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
