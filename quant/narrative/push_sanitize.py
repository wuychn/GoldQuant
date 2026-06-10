"""飞书推送正文用语清洗：去掉 LLM 误带的内部标签前缀。"""

from __future__ import annotations

import re

from quant.narrative.push_style import (
    DEPRECATED_PUSH_TERMS,
    DEPRECATED_TERM_REPLACEMENTS,
    PUSH_OPS_SECTION_MARKERS,
)

# 误带入正文的内部前缀 / 字段名
_STRIP_PREFIXES = (
    "研判中的",
    "研判要点中的",
    "研判要点：",
    "研判要点:",
    "写作参考中的",
    "程序结论中的",
)
_RE_INTERNAL_PHRASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"当日涨幅榜概念"), "当日涨幅靠前的概念"),
    (re.compile(r"当日资金流入榜概念"), "当日资金流入靠前的概念"),
    (re.compile(r"【研判要点[^】]*】"), ""),
    (re.compile(r"【历史叙述参考[^】]*】"), ""),
    (re.compile(r"【接口数据\s*JSON】"), ""),
    (re.compile(r"<<<[^>]+>>>"), ""),
    # 旧推送表述 → 新口径
    (re.compile(r"市场环境(?:正常|良好|一般|偏弱|较差|偏强)?"), "赚钱效应一般"),
    (re.compile(r"市场档位[：:]?\s*强势"), "赚钱效应强"),
    (re.compile(r"市场档位[：:]?\s*震荡"), "赚钱效应一般"),
    (re.compile(r"市场档位[：:]?\s*弱势"), "赚钱效应差"),
    (re.compile(r"市场档位"), "赚钱效应"),
    (re.compile(r"市场状态[：:]?\s*强势"), "赚钱效应强"),
    (re.compile(r"市场状态[：:]?\s*震荡"), "赚钱效应一般"),
    (re.compile(r"市场状态[：:]?\s*弱势"), "赚钱效应差"),
    (re.compile(r"交易环境"), "仓位控制"),
    (re.compile(r"市场强势"), "赚钱效应强"),
    (re.compile(r"市场震荡"), "赚钱效应一般"),
    (re.compile(r"市场弱势"), "赚钱效应差"),
    (re.compile(r"，可参与交易"), ""),
    (re.compile(r"可参与交易"), ""),
    (re.compile(r"一、全天大盘"), "一、大盘概况"),
    (re.compile(r"一、今日开盘概况"), "一、大盘概况"),
    (re.compile(r"一、上午大盘"), "一、大盘概况"),
    (re.compile(r"一、市场概况"), "一、大盘概况"),
    (re.compile(r"二、主线复盘"), "一、大盘概况"),
    (re.compile(r"二、主线与概念"), "一、大盘概况"),
    (re.compile(r"二、主线变化"), "一、大盘概况"),
    (re.compile(r"主线龙头"), "主升波段"),
    (re.compile(r"确认主线"), "概念板块"),
    (re.compile(r"涨幅主线"), "涨幅靠前概念"),
    (re.compile(r"资金主线"), "资金流入概念"),
    (re.compile(r"主线题材"), "概念板块"),
    (re.compile(r"当前主线"), "概念板块"),
    (re.compile(r"主线复盘"), "大盘概况"),
    (re.compile(r"三、持仓跟踪"), "三、持仓股表现"),
    (re.compile(r"七、自选更新"), "八、自选更新"),
]
_RE_BLANK_LINES = re.compile(r"\n{3,}")
_RE_INLINE_OPS_LINE = re.compile(r"(?m)^(?:今日)?操作[：:].+\n?")
_RE_NO_TRADE_LINE = re.compile(r"(?m)^(?:今日)?无买卖(?:操作)?[。.]?\s*\n?")


def _protected_ops_boundary(text: str) -> int:
    """允许出现买卖表述的最前位置（操作复盘/操作/自选更新段）。"""
    idx = len(text)
    for marker in PUSH_OPS_SECTION_MARKERS:
        pos = text.find(marker)
        if pos != -1 and pos < idx:
            idx = pos
    return idx


def _strip_inline_ops_lines(text: str) -> str:
    """正文误带的「操作：…」行仅保留在操作专节。"""
    idx = _protected_ops_boundary(text)
    head = text if idx >= len(text) else text[:idx]
    tail = "" if idx >= len(text) else text[idx:]
    head = _RE_INLINE_OPS_LINE.sub("", head)
    head = _RE_NO_TRADE_LINE.sub("", head)
    return head + tail


def sanitize_feishu_body(text: str) -> str:
    if not text or not text.strip():
        return text
    out = text
    for term in DEPRECATED_PUSH_TERMS:
        if term == "可参与交易":
            continue
        repl = DEPRECATED_TERM_REPLACEMENTS.get(term, "仓位控制")
        out = out.replace(term, repl)
    out = re.sub(r"(?<![\u4e00-\u9fff])主线(?![\u4e00-\u9fff])", "趋势", out)
    out = re.sub(r"(?<![\u4e00-\u9fff])龙头(?![\u4e00-\u9fff])", "标的", out)
    for p in _STRIP_PREFIXES:
        out = out.replace(p, "")
    for pat, repl in _RE_INTERNAL_PHRASES:
        out = pat.sub(repl, out)
    out = _strip_inline_ops_lines(out)
    out = re.sub(r"(?m)^本轮无操作信号[。.]?.*\n?", "", out)
    return _RE_BLANK_LINES.sub("\n\n", out).strip()
