"""飞书推送正文用语清洗：去掉 LLM 误带的内部标签前缀。"""

from __future__ import annotations

import re

from quant.narrative.push_style import DEPRECATED_PUSH_TERMS

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
]
_RE_BLANK_LINES = re.compile(r"\n{3,}")


def sanitize_feishu_body(text: str) -> str:
    if not text or not text.strip():
        return text
    out = text
    for term in DEPRECATED_PUSH_TERMS:
        if term == "可参与交易":
            continue
        out = out.replace(term, "赚钱效应" if term in ("市场环境", "市场档位", "市场状态") else "仓位控制")
    for p in _STRIP_PREFIXES:
        out = out.replace(p, "")
    for pat, repl in _RE_INTERNAL_PHRASES:
        out = pat.sub(repl, out)
    return _RE_BLANK_LINES.sub("\n\n", out).strip()
