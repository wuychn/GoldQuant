"""R2 编排入口：phase 处理 + 飞书推送（新闻/盯盘/复盘/研报）。"""

from __future__ import annotations

from quant.data_fetch import fetch_mode
from quant.io import state as state_io
from quant.narrative.during_market_push import build_during_market_push
from quant.narrative.engine_brief import build_engine_brief
from quant.narrative.llm import call_llm
from quant.narrative.prompts import (
    build_user_msg,
    prompt_evening_review,
    prompt_lunch_review,
    prompt_pre_market,
)
from quant.narrative.push_sanitize import sanitize_feishu_body
from quant.narrative.stock_lines import WATCHLIST_SECTION_TITLE, build_watchlist_push_section
from quant.pipeline.news import run_news
from quant.pipeline.phases import (
    run_during_market,
    run_evening,
    run_lunch,
    run_pre_market,
)
from quant.progress_log import configure_progress_logging, log_progress_error
from quant.push.service import MODE_LABELS, push_message
from quant.research.report import build_evening_research
from quant.scoring.context import ScoreContext
from quant.store.snapshot import save_review
from quant.timeutil import cn_datetime_str


_MODE_HANDLERS = {
    "pre_market": run_pre_market,
    "during_market": run_during_market,
    "post_market_lunch": run_lunch,
    "post_market_evening": run_evening,
}


def _llm_review(mode: str, payload: dict, ts: str, *, extra: str = "") -> str:
    ctx = ScoreContext.from_payload(payload, mode=mode)
    brief = build_engine_brief(ctx, payload, mode=mode)
    user = build_user_msg(payload, mode=mode, engine_brief=brief, extra=extra)
    prompts = {
        "pre_market": prompt_pre_market,
        "post_market_lunch": prompt_lunch_review,
        "post_market_evening": prompt_evening_review,
    }
    system = prompts[mode]()
    msg = call_llm(system, user) or brief
    return sanitize_feishu_body(msg, push_timestamp=ts)


def _evening_watchlist_section(payload: dict) -> str:
    """R2 双池 → 推送展示用自选段。"""
    rows: list[dict] = []
    for m in state_io.load_combat() + state_io.load_tracking():
        snap = dict(m.snapshot or {})
        snap.update(
            {
                "股票代码": m.code,
                "股票名称": m.name,
                "pool_stage": m.stage.value,
                "alpha_score": m.alpha_score,
                "structure_score": m.structure_score,
                "sector_tags": m.sector_tags,
                "加入自选原因": m.reason,
            }
        )
        rows.append(snap)
    if not rows:
        return ""
    return build_watchlist_push_section(rows, [], [], title=WATCHLIST_SECTION_TITLE)


def run_mode(mode: str, timestamp: str | None = None) -> None:
    configure_progress_logging()
    ts = timestamp or cn_datetime_str()

    if mode == "news":
        raw = fetch_mode(mode)
        summary = run_news(raw, timestamp=ts)
        if summary:
            body = sanitize_feishu_body(summary, push_timestamp=ts)
            push_message(MODE_LABELS["news"], body, timestamp=ts)
        return

    handler = _MODE_HANDLERS.get(mode)
    if not handler:
        raise ValueError(f"R2 不支持模式: {mode}")

    try:
        raw = fetch_mode(mode)
    except Exception as e:
        log_progress_error(mode, f"拉取数据失败: {e}")
        raise

    result = handler(raw, timestamp=ts)
    payload = result.get("payload") or {}

    if mode == "during_market":
        msg = build_during_market_push(
            payload,
            timestamp=ts,
            raw_buy=result.get("raw_buy"),
            raw_sell=result.get("raw_sell"),
            executable=result.get("executable"),
            executed=result.get("executed"),
            audit=result.get("audit"),
            rejected=result.get("rejected"),
            ctx=result.get("ctx"),
            mode=mode,
        )
        msg = sanitize_feishu_body(msg, push_timestamp=ts)
        save_review(mode, msg)
        push_message(MODE_LABELS["during_market"], msg, timestamp=ts)
        return

    if mode == "post_market_evening":
        research = result.get("research") or ""
        extra = f"\n\n{research}\n" if research else ""
        msg = _llm_review(mode, payload, ts, extra=extra)
        watch = _evening_watchlist_section(payload)
        if watch:
            msg = f"{msg}\n\n{watch}"
        pool_stats = result.get("stats") or {}
        if pool_stats:
            msg += (
                f"\n\n双池 tracking={pool_stats.get('tracking', 0)} "
                f"combat={pool_stats.get('combat', 0)}"
            )
        save_review(mode, msg)
        push_message(MODE_LABELS["post_market_evening"], msg, timestamp=ts)
        return

    if mode in ("pre_market", "post_market_lunch"):
        msg = _llm_review(mode, payload, ts)
        save_review(mode, msg)
        push_message(MODE_LABELS[mode], msg, timestamp=ts)
