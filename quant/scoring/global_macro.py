"""全球宏观/地缘评分：news 模式 LLM 一次判定，智能盯盘只读缓存。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from quant.narrative.llm import call_llm
from quant.store.state import read_global_macro, write_global_macro

_VALID_SENTIMENTS = frozenset({"bearish", "neutral", "bullish"})
_DEFAULT_SCORES = {"bearish": 5.0, "neutral": 50.0, "bullish": 95.0}

_PROMPT = (
    "根据以下「当日新闻综合解读」，判断对 A 股短线（1～3 日）的全球宏观/地缘影响。\n"
    "只输出一行 JSON，不要 markdown、不要其它文字：\n"
    '{"sentiment":"bearish|neutral|bullish","reason":"30字以内"}\n\n'
    "判定口径：\n"
    "- bearish：地缘冲突升级、海外股市大跌、原油等大宗暴涨冲击、制裁/关税升级等对 A 股偏空\n"
    "- bullish：海外风险偏好回升、重大政策或流动性利好、海外主要市场大涨等对 A 股偏多\n"
    "- neutral：影响不大、多空交织、或已在指数中充分反映\n"
)


def sentiment_to_score(sentiment: str, *, scores: dict[str, float] | None = None) -> float:
    table = scores or _DEFAULT_SCORES
    key = str(sentiment or "").strip().lower()
    return float(table.get(key, table["neutral"]))


def parse_global_macro_response(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    for chunk in (raw, _extract_json_object(raw)):
        if not chunk:
            continue
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        sentiment = str(data.get("sentiment") or "").strip().lower()
        if sentiment not in _VALID_SENTIMENTS:
            continue
        reason = str(data.get("reason") or "").strip()[:80]
        score = sentiment_to_score(sentiment)
        return {
            "sentiment": sentiment,
            "score": score,
            "reason": reason,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    return None


def _extract_json_object(text: str) -> str:
    m = re.search(r"\{[^{}]*\}", text, flags=re.DOTALL)
    return m.group(0) if m else ""


def refresh_global_macro_from_summary(summary: str) -> dict[str, Any] | None:
    """基于 news_summary 调用 LLM 刷新 ~/.quant/memory/global_macro.json。"""
    body = (summary or "").strip()
    if not body:
        return None
    try:
        resp = call_llm(_PROMPT, body[:2000], max_tokens=256, temperature=0.1)
    except Exception:
        return None
    parsed = parse_global_macro_response(resp)
    if parsed:
        write_global_macro(parsed)
    return parsed


def global_macro_for_scoring() -> dict[str, Any] | None:
    data = read_global_macro()
    if not isinstance(data, dict):
        return None
    sentiment = str(data.get("sentiment") or "").strip().lower()
    if sentiment not in _VALID_SENTIMENTS:
        return None
    try:
        score = float(data.get("score"))
    except (TypeError, ValueError):
        score = sentiment_to_score(sentiment)
    return {
        "sentiment": sentiment,
        "score": score,
        "reason": str(data.get("reason") or "").strip(),
        "updated_at": str(data.get("updated_at") or "").strip(),
    }
