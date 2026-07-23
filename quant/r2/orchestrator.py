"""R2 编排入口。"""

from __future__ import annotations

import asyncio

from quant.data_fetch import fetch_mode
from quant.narrative.engine_brief import build_engine_brief
from quant.narrative.llm import call_llm
from quant.narrative.prompts import (
    prompt_evening_review,
    prompt_lunch_review,
    prompt_pre_market,
)
from quant.progress_log import configure_progress_logging, log_progress_error
from quant.r2.pipeline.news import run_news
from quant.r2.pipeline.phases import (
    run_during_market,
    run_evening,
    run_lunch,
    run_pre_market,
)
from quant.store.snapshot import save_review
from quant.timeutil import cn_datetime_str


_MODE_HANDLERS = {
    "pre_market": run_pre_market,
    "during_market": run_during_market,
    "post_market_lunch": run_lunch,
    "post_market_evening": run_evening,
}


def run_mode(mode: str, timestamp: str | None = None) -> None:
    configure_progress_logging()
    ts = timestamp or cn_datetime_str()
    handler = _MODE_HANDLERS.get(mode)
    if not handler:
        if mode == "news":
            raw = __import__("asyncio").run(fetch_mode(mode))
            run_news(raw, timestamp=ts)
            return
        raise ValueError(f"R2 不支持模式: {mode}")

    try:
        raw = asyncio.run(fetch_mode(mode))
    except Exception as e:
        log_progress_error(mode, f"拉取数据失败: {e}")
        raise

    result = handler(raw, timestamp=ts)
    payload = result.get("payload") or {}

    if mode == "pre_market":
        brief = build_engine_brief(payload, mode=mode)
        msg = call_llm(prompt_pre_market(brief), scope=mode) or brief
        save_review(mode, msg)
    elif mode == "post_market_lunch":
        brief = build_engine_brief(payload, mode=mode)
        msg = call_llm(prompt_lunch_review(brief), scope=mode) or brief
        save_review(mode, msg)
    elif mode == "post_market_evening":
        brief = build_engine_brief(payload, mode=mode)
        msg = call_llm(prompt_evening_review(brief), scope=mode) or brief
        save_review(mode, msg)
