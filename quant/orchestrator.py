"""五模式编排：拉数 → 评分/三确认信号 → 落盘 → LLM 文案 → 飞书。

策略与交易决策均由规则引擎完成；LLM 只读「程序结论」生成叙述，不参与决策。
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta

from app.utils.common_util import is_real_workday_cn

from app.core.config import get_settings

from quant.data_fetch import fetch_mode, fixture_path_for_mode, unwrap_payload
from quant.progress_log import configure_progress_logging, log_progress, log_progress_done, log_progress_error
from quant.timeutil import cn_datetime_str, cn_today
from quant.constants import STRATEGY_NAME
from quant.execution.executor import ExecutedTrade, execute_signals
from quant.narrative.during_market_push import build_during_market_push
from quant.narrative.engine_brief import build_engine_brief
from quant.narrative.stock_lines import (
    build_watchlist_human_reason,
    build_watchlist_push_section,
    refresh_merged_watchlist_reasons,
)
from quant.narrative.llm import call_llm
from quant.narrative.prompts import (
    build_user_msg,
    prompt_evening_review,
    prompt_lunch_review,
    prompt_news,
    prompt_pre_market,
)
from quant.pool.builder import build_candidates
from quant.scoring.theme_tracker import update_concept_tracker_state
from quant.pool.ths_rank_util import stock_ths_rank_tags
from quant.narrative.push_sanitize import sanitize_feishu_body
from quant.push.feishu import get_token, send_msg
from quant.push.format import format_push_message
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine
from quant.scoring.global_macro import refresh_global_macro_from_summary
from quant.signals.pipeline import generate_confirmed_signals
from quant.store.snapshot import save_derived, save_raw, save_review
from quant.store.state import (
    append_lesson,
    get_holdings,
    get_optional,
    merge_payload_holdings,
    save_optional,
    write_news_summary,
)
from quant.store.watchlist import merge_watchlist_evening, watchlist_retain_days

_MODE_LABELS = {
    "news": "新闻聚焦",
    "pre_market": "开盘啦",
    "during_market": "智能盯盘",
    "post_market_lunch": "午间复盘",
    "post_market_evening": "晚间复盘",
}


def _prepare_payload(raw: dict, *, mode: str = "") -> dict:
    return merge_payload_holdings(unwrap_payload(raw))


def _build_operation_section(
    executed: list[ExecutedTrade],
    *,
    section: str,
    no_trade_detail: str = "",
) -> str:
    lines = [section]
    if not executed:
        detail = no_trade_detail.strip()
        lines.append(detail if detail else "继续观望，暂不加减仓。")
        return "\n".join(lines)
    for e in executed:
        s = e.signal
        px = e.fill_price if e.fill_price > 0 else s.price
        fee_parts = []
        if e.commission:
            fee_parts.append(f"佣{e.commission:.2f}")
        if e.stamp_tax:
            fee_parts.append(f"税{e.stamp_tax:.2f}")
        if e.transfer_fee:
            fee_parts.append(f"过户{e.transfer_fee:.2f}")
        fee_note = f"，{'/'.join(fee_parts)}" if fee_parts else ""
        pnl = f"，盈亏：{e.pnl:+.2f}元" if e.pnl else ""
        lines.append(
            f"· {s.action}{s.name}（{s.code}），"
            f"时间：{e.timestamp}，"
            f"成交价：{px:.2f}{fee_note}，"
            f"数量：{s.quantity // 100}手，"
            f"战法：{s.strategy}，"
            f"理由：{s.reason}{pnl}"
        )
    return "\n".join(lines)


def _update_watchlist_evening(ctx: ScoreContext) -> tuple[list[dict], list[dict], str, list]:
    """晚间复盘：达标写入自选；未达标但末次入选≤N 个交易日仍保留，超期移出。"""
    scope = "post_market_evening"
    retain = watchlist_retain_days()
    log_progress(scope, "合并三来源候选")
    engine = ScoringEngine()
    candidates = build_candidates(ctx.payload)
    log_progress(scope, "候选池评分", detail=f"共 {len(candidates)} 只")
    by_code = {str(c.get("股票代码", "")).strip(): c for c in candidates}
    scores = engine.apply_threshold(
        engine.score_many(ctx, candidates),
        kind="watchlist",
    )
    passed = sorted(
        [s for s in scores if s.passed_threshold],
        key=lambda x: x.total,
        reverse=True,
    )
    log_progress(scope, "评分完成", detail=f"达标 {len(passed)}/{len(scores)} 只")

    passed_rows: list[dict] = []
    for s in passed:
        cand = by_code.get(s.code, {})
        row = {
            "股票代码": s.code,
            "股票名称": s.name,
            "战法": STRATEGY_NAME,
            "评分": round(s.total, 2),
            "加入自选原因": build_watchlist_human_reason(s, cand),
        }
        ths_tags = stock_ths_rank_tags(cand)
        if ths_tags:
            row["榜单标签"] = ths_tags
        passed_rows.append(row)

    existing = get_optional()
    merged, added, removed = merge_watchlist_evening(existing, passed_rows)
    score_by_code = {s.code: s for s in scores}
    refresh_merged_watchlist_reasons(
        merged,
        score_by_code=score_by_code,
        candidate_by_code=by_code,
    )
    save_optional(merged, delta={"added": added, "removed": removed})
    log_progress(
        scope,
        "写入自选（滚动保留）",
        detail=f"共 {len(merged)} 只，新增 {len(added)}，移出 {len(removed)}，保留 {retain} 交易日",
    )

    save_derived("scores_watchlist.json", [s.to_dict() for s in scores])
    save_derived(
        "optional_delta.json",
        {"added": added, "removed": removed, "total": len(merged), "retain_days": retain},
    )

    optional_section = build_watchlist_push_section(merged, added, removed)
    return merged, added, optional_section, scores


def process_news(raw: dict, timestamp: str) -> str:
    scope = "news"
    payload = unwrap_payload(raw)
    news_list = payload if isinstance(payload, list) else [payload]
    log_progress(scope, "LLM 新闻解读", detail=f"共 {len(news_list)} 条")
    user = json.dumps({"news": news_list}, ensure_ascii=False)[:140000]
    summary = call_llm(prompt_news(), user, max_tokens=4000)
    news_summary_text = ""
    if "综合解读" in summary:
        tail = summary.split("综合解读", 1)[-1]
        news_summary_text = f"综合解读{tail.strip()[:800]}"
        write_news_summary(news_summary_text)
    if news_summary_text:
        log_progress(scope, "全球宏观评分")
        macro = refresh_global_macro_from_summary(news_summary_text)
        if macro:
            log_progress(
                scope,
                "全球宏观评分完成",
                detail=f"{macro.get('sentiment')} {macro.get('score')}",
            )
        else:
            log_progress(scope, "全球宏观评分跳过", detail="LLM 未返回有效 JSON")
    log_progress_done(scope, "新闻分析完成")
    return summary


def process_pre_market(raw: dict) -> str:
    scope = "pre_market"
    log_progress(scope, "开始开盘啦分析")
    payload = _prepare_payload(raw, mode=scope)
    ctx = ScoreContext.from_payload(payload, mode="pre_market")

    log_progress(scope, "生成买卖信号（盘前不计三确认，仅落盘）")
    raw_buy, raw_sell, executable, audit = generate_confirmed_signals(ctx, mode="pre_market")
    brief = build_engine_brief(ctx, payload, mode="pre_market")

    log_progress(scope, "LLM 盘前文案")
    narrative = call_llm(
        prompt_pre_market(),
        build_user_msg(payload, mode="pre_market", engine_brief=brief),
        max_tokens=8000,
    )
    save_derived(
        "signals.json",
        {
            "raw_buy": [asdict(s) for s in raw_buy],
            "raw_sell": [],
            "executable": [asdict(s) for s in executable],
            "confirmation_audit": audit,
        },
    )
    log_progress_done(scope, "开盘啦分析完成", detail=f"可执行 {len(executable)} 条")
    return narrative.rstrip()


def _sync_account_for_brief(payload: dict) -> None:
    from quant.narrative.holdings_context import holdings_for_pnl
    from quant.store.state import refresh_account_market_value

    refresh_account_market_value(holdings_for_pnl(payload))


def process_during_market(raw: dict, *, timestamp: str = "") -> str:
    scope = "during_market"
    log_progress(scope, "开始盘中分析")
    payload = _prepare_payload(raw, mode=scope)
    ctx = ScoreContext.from_payload(payload, mode="during_market")

    log_progress(scope, "生成买卖信号")
    raw_buy, raw_sell, executable, audit = generate_confirmed_signals(ctx, mode="during_market")
    log_progress(scope, "执行模拟成交", detail=f"可执行 {len(executable)} 条")
    executed = execute_signals(executable, payload=payload) if executable else []

    _sync_account_for_brief(payload)
    engine = ScoringEngine()
    log_progress(scope, "持仓评分")
    holding_scores = engine.score_many(ctx, payload.get("持仓股") or get_holdings())
    save_derived("scores_holding.json", [s.to_dict() for s in holding_scores])
    save_derived(
        "signals.json",
        {
            "raw_buy": [asdict(s) for s in raw_buy],
            "raw_sell": [asdict(s) for s in raw_sell],
            "executable": [asdict(s) for s in executable],
            "confirmation_audit": audit,
        },
    )

    log_progress(scope, "模板化推送文案")
    ts = timestamp or cn_datetime_str()
    body = build_during_market_push(
        payload,
        timestamp=ts,
        raw_buy=raw_buy,
        raw_sell=raw_sell,
        executable=executable,
        executed=executed,
        audit=audit,
        ctx=ctx,
        mode=scope,
    )
    log_progress_done(scope, "盘中分析完成", detail=f"成交 {len(executed)} 笔")
    return body


def process_lunch_review(raw: dict) -> str:
    scope = "post_market_lunch"
    log_progress(scope, "开始午间复盘")
    payload = _prepare_payload(raw, mode=scope)
    ctx = ScoreContext.from_payload(payload, mode="post_market_lunch")

    _sync_account_for_brief(payload)
    brief = build_engine_brief(ctx, payload, mode="post_market_lunch")
    log_progress(scope, "LLM 午间文案")
    narrative = call_llm(
        prompt_lunch_review(),
        build_user_msg(payload, mode="post_market_lunch", engine_brief=brief),
        max_tokens=8000,
        temperature=0.1,
    )
    log_progress_done(scope, "午间复盘完成")
    return narrative.rstrip()


def process_evening_review(raw: dict) -> str:
    scope = "post_market_evening"
    payload = _prepare_payload(raw, mode=scope)
    log_progress(scope, "更新概念板块快照 concept_tracker.json")
    update_concept_tracker_state(payload)
    ctx = ScoreContext.from_payload(payload, mode="post_market_evening")

    log_progress(scope, "自选池更新与评分")
    merged, added, optional_section, scores = _update_watchlist_evening(ctx)
    _sync_account_for_brief(payload)
    log_progress(scope, "生成引擎摘要")
    brief = build_engine_brief(
        ctx,
        payload,
        mode="post_market_evening",
        watchlist_scores=scores,
        watchlist_pool=merged,
        watchlist_added=added,
    )
    log_progress(scope, "LLM 晚间复盘文案")
    narrative = call_llm(
        prompt_evening_review(),
        build_user_msg(payload, mode="post_market_evening", engine_brief=brief),
        max_tokens=9000,
        temperature=0.1,
    )
    log_progress_done(scope, "晚间复盘分析完成")
    return narrative.rstrip() + "\n\n" + optional_section


def pipeline_allowed_for_mode(mode: str, *, on: date | None = None) -> bool:
    """是否应运行该模式的完整流水线（与 FastAPI 定时任务一致，基于 `is_real_workday_cn`）。

    - 新闻：始终允许。
    - 盘前/盘中/午间：仅当日为大陆真实工作日。
    - 晚间复盘：当日为工作日，或「当日非工作日但次日为工作日」（节假日前夜备盘口径）。
    """
    if mode == "news":
        return True
    d = on if on is not None else cn_today()
    if mode == "post_market_evening":
        return is_real_workday_cn(d) or is_real_workday_cn(d + timedelta(days=1))
    return is_real_workday_cn(d)


def run_mode(mode: str, timestamp: str) -> None:
    """单次运行完整流水线：fetch → process → save → feishu。"""
    configure_progress_logging()
    settings = get_settings()
    label = _MODE_LABELS.get(mode, mode)
    log_progress(mode, f"开始 {label}", detail=timestamp)

    if not settings.QUANT_TEST_PHASE and not pipeline_allowed_for_mode(mode):
        log_progress(mode, "跳过：当前日期/模式不满足交易日历")
        return

    try:
        log_progress(mode, "拉取数据")
        raw = fetch_mode(mode)
        if settings.QUANT_USE_LOCAL_FIXTURE:
            log_progress(mode, "本地 fixture 已加载", detail=str(fixture_path_for_mode(mode)))
        else:
            log_progress(mode, "HTTP API 拉取成功")
    except Exception as e:
        from app.utils.error_log import format_error_detail

        log_progress_error(mode, "数据拉取失败", detail=format_error_detail("fetch_mode", e))
        sys.exit(1)

    log_progress(mode, "保存原始快照")
    payload = _prepare_payload(raw, mode=mode)
    save_raw(mode, payload)

    try:
        log_progress(mode, "分析处理")
        if mode == "news":
            body = process_news(raw, timestamp)
        elif mode == "pre_market":
            body = process_pre_market(raw)
        elif mode == "during_market":
            body = process_during_market(raw, timestamp=timestamp)
        elif mode == "post_market_lunch":
            body = process_lunch_review(raw)
        elif mode == "post_market_evening":
            body = process_evening_review(raw)
        else:
            log_progress(mode, f"未知模式: {mode}")
            sys.exit(1)
    except Exception as e:
        from app.utils.error_log import format_error_detail

        log_progress_error(mode, "分析失败", detail=format_error_detail("process", e))
        body = f"服务异常，请稍后重试。({e})"

    log_progress(mode, "保存复盘文案")
    if mode == "during_market":
        message = sanitize_feishu_body(body, push_timestamp=timestamp).strip() + "\n"
    else:
        message = format_push_message(label, timestamp, sanitize_feishu_body(body, push_timestamp=timestamp))
    save_review(mode, message)

    if mode == "post_market_evening":
        try:
            log_progress(mode, "提炼经验教训")
            lesson = call_llm(
                "提取本次复盘中的1-3条可执行经验教训，80字以内，纯文本。",
                body[-3000:],
                max_tokens=300,
            )
            append_lesson(lesson.strip())
        except Exception as e:
            from app.utils.error_log import format_error_detail

            log_progress_error(mode, "经验提炼失败", detail=format_error_detail("lesson", e))

    try:
        log_progress(mode, "飞书推送")
        token = get_token()
        send_msg(message, token)
        log_progress_done(mode, "飞书推送成功")
    except Exception as e:
        from app.utils.error_log import format_error_detail

        log_progress_error(mode, "飞书推送失败", detail=format_error_detail("feishu", e))

    log_progress_done(mode, f"{label} 全流程结束")
    print("\n" + "=" * 60)
    print(message[:2000] if len(message) > 2000 else message)
    print("=" * 60)
