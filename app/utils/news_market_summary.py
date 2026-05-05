"""新闻接口触发的「对股市有影响」短文摘要，写入 ~/data/quant/news_market_impact_summary.txt。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

_SUMMARY_FILENAME = "news_market_impact_summary.txt"
_MAX_CHARS = 500


def news_market_summary_path() -> Path:
    return Path.home() / "data" / "quant" / _SUMMARY_FILENAME


def _truncate_zh(text: str, max_chars: int) -> str:
    t = text.strip()
    if len(t) <= max_chars:
        return t
    return t[:max_chars]


def _llm_minimax_summary(api_key: str, base_url: str, model: str, news_json: str) -> str | None:
    url = f"{base_url.rstrip('/')}/v1/messages"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    system = (
        "你是资深 A 股资讯编辑。请根据下列多渠道财经新闻（可能有重复），写出一段"
        f"对当日沪深股市可能有影响的浓缩摘要，纯文本，不超过{_MAX_CHARS}个汉字或字符，"
        "禁止 markdown、禁止编号列表符号。聚焦：宏观与政策、行业与板块、风险偏好与情绪。"
        "若信息噪声过大，概括为主。"
    )
    user = f"新闻原始 JSON（节选）：\n{news_json[:120000]}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": f"{system}\n\n{user}"}],
        "max_tokens": 900,
        "temperature": 0.25,
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            for c in data.get("content", []):
                if c.get("type") == "text":
                    return str(c.get("text") or "").strip()
            return None
    except Exception:
        logger.exception("新闻影响摘要 LLM 调用失败")
        return None


def refresh_news_market_summary_sync(settings: Settings, news_items: list) -> None:
    """同步调用；无密钥则跳过。"""
    key = (settings.QUANT_NEWS_SUMMARY_LLM_API_KEY or "").strip()
    if not key:
        return
    base = settings.QUANT_NEWS_SUMMARY_LLM_BASE_URL.strip()
    model = settings.QUANT_NEWS_SUMMARY_LLM_MODEL.strip()
    raw = json.dumps(news_items, ensure_ascii=False)
    text = _llm_minimax_summary(key, base, model, raw)
    if not text:
        return
    out = _truncate_zh(text, _MAX_CHARS)
    path = news_market_summary_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(out, encoding="utf-8")
    except OSError:
        logger.exception("写入新闻影响摘要失败 path=%s", path)
