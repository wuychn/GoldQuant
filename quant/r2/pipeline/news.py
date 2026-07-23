"""新闻模式（与 R1 API 兼容，无策略决策）。"""

from __future__ import annotations

import json

from quant.data_fetch import unwrap_payload
from quant.narrative.llm import call_llm
from quant.narrative.prompts import prompt_news
from quant.progress_log import log_progress, log_progress_done
from quant.scoring.global_macro import refresh_global_macro_from_summary
from quant.store.snapshot import save_raw, save_review
from quant.store.state import write_news_summary


def run_news(raw: dict, *, timestamp: str) -> str:
    scope = "news"
    payload = unwrap_payload(raw)
    news_list = payload if isinstance(payload, list) else payload.get("news") if isinstance(payload, dict) else []
    if not isinstance(news_list, list):
        news_list = [payload]
    log_progress(scope, "LLM 新闻解读", detail=f"共 {len(news_list)} 条")
    user = json.dumps({"news": news_list}, ensure_ascii=False)[:140000]
    summary = call_llm(prompt_news(), user, max_tokens=4000) or ""
    news_summary_text = ""
    if "综合解读" in summary:
        tail = summary.split("综合解读", 1)[-1]
        news_summary_text = f"综合解读{tail.strip()[:800]}"
        write_news_summary(news_summary_text)
    if news_summary_text:
        macro = refresh_global_macro_from_summary(news_summary_text)
        if macro:
            log_progress(scope, "全球宏观评分完成", detail=str(macro.get("score")))
    save_raw("news", {"news": news_list})
    save_review("news", summary)
    log_progress_done(scope, "新闻分析完成")
    return summary
