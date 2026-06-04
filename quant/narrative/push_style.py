"""飞书推送与自然叙述共用的文案约定。"""

from __future__ import annotations

# 注入 LLM 的 engine_brief 分隔行（勿使用会在正文出现的「研判」等字样）
BRIEF_PREAMBLE = (
    "（以下为写作参考，勿将本节标题及【】标签原样写入推送正文；"
    "正文用自然财经表述，如「当日涨幅」「资金流入」「主线龙头」）"
)
