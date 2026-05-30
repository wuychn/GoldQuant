"""五模式编排：拉数 → 评分/三确认信号 → 落盘 → LLM 文案 → 飞书。

策略与交易决策均由规则引擎完成（主线/龙头/买卖/加自选）；LLM 只读「程序结论」生成叙述，不参与决策。
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta

from app.utils.common_util import is_real_workday_cn

from app.core.config import get_settings

from quant.data_fetch import fetch_mode, fixture_path_for_mode, unwrap_payload
from quant.constants import STRATEGY_NAME
from quant.execution.executor import ExecutedTrade, execute_signals
from quant.gates.rules import check_global_gates
from quant.narrative.engine_brief import build_engine_brief
from quant.narrative.llm import call_llm
from quant.narrative.prompts import (
    build_user_msg,
    prompt_during_market,
    prompt_evening_review,
    prompt_lunch_review,
    prompt_news,
    prompt_pre_market,
)
from quant.pool.builder import build_candidates
from quant.pool.pkyd_util import stock_pkyd_tags
from quant.push.feishu import get_token, send_msg
from quant.push.format import format_push_message
from quant.scoring.context import ScoreContext, infer_regime
from quant.scoring.engine import ScoringEngine
from quant.signals.pipeline import generate_confirmed_signals
from quant.store.snapshot import save_derived, save_raw, save_review
from quant.store.state import (
    append_lesson,
    get_optional,
    save_optional,
    write_news_summary,
)

_MODE_LABELS = {
    "news": "新闻聚焦",
    "pre_market": "盘前分析",
    "during_market": "盘中实时",
    "post_market_lunch": "午间复盘",
    "post_market_evening": "晚间复盘",
}


def _prepare_payload(raw: dict) -> dict:
    return unwrap_payload(raw)


def _build_operation_section(
    executed: list[ExecutedTrade],
    *,
    section: str,
    no_trade_detail: str = "",
) -> str:
    lines = [section]
    if not executed:
        detail = no_trade_detail.strip()
        lines.append(f"本轮无操作信号。{detail}" if detail else "本轮无操作信号。")
        return "\n".join(lines)
    for e in executed:
        s = e.signal
        pnl = f"，盈亏：{e.pnl:+.2f}元" if e.pnl else ""
        lines.append(
            f"· {s.action}{s.name}（{s.code}），"
            f"时间：{e.timestamp}，"
            f"价格：{s.price:.2f}，"
            f"数量：{s.quantity // 100}手，"
            f"战法：{s.strategy}，"
            f"理由：{s.reason}{pnl}"
        )
    return "\n".join(lines)


def _no_trade_reason(mode: str, ctx: ScoreContext, buy_n: int, sell_n: int) -> str:
    """无成交说明：纯规则引擎事实，不经 LLM。"""
    gates = check_global_gates(ctx).summary()
    regime = infer_regime(ctx.payload)
    if buy_n == 0 and sell_n == 0:
        return f"原因：程序未产生买卖原始信号；市场状态{regime}；{gates}。"
    return (
        f"原因：有原始信号但未成交（买入{buy_n}条/卖出{sell_n}条，"
        f"可能未过三确认或门禁）；市场状态{regime}；{gates}。"
    )


def _score_summary_lines(scores: list) -> list[str]:
    lines = []
    for s in sorted(scores, key=lambda x: x.total, reverse=True)[:10]:
        mark = "✓" if s.passed_threshold else "×"
        lines.append(f"· {mark} {s.name}({s.code}) {s.total:.1f}分 [{STRATEGY_NAME}]")
    return lines


def _watchlist_add_reason(score, candidate_row: dict) -> str:
    parts = [f"评分{score.total:.1f}"]
    source = str(candidate_row.get("候选来源") or "").strip()
    if source == "盘口异动":
        tags = "、".join(stock_pkyd_tags(candidate_row)) or "盘口异动"
        parts.append(f"盘口异动({tags})+概念共振")
    else:
        tags = stock_pkyd_tags(candidate_row)
        if tags:
            parts.append(f"盘口异动({ '、'.join(tags) })")
        parts.append("主线主升浪龙头")
    return "；".join(parts)


def _update_watchlist_evening(ctx: ScoreContext) -> tuple[list[dict], str]:
    """晚间复盘：对候选池评分，达标者 append 到 state/optional.jsonl。"""
    engine = ScoringEngine()
    candidates = build_candidates(ctx.payload)
    by_code = {str(c.get("股票代码", "")).strip(): c for c in candidates}
    scores = engine.apply_threshold(
        engine.score_many(ctx, candidates),
        kind="watchlist",
        stock_rows=by_code,
    )
    passed = [s for s in scores if s.passed_threshold]

    existing = get_optional()
    codes = {str(r.get("股票代码", "")).strip() for r in existing}
    added: list[dict] = []
    for s in passed:
        if s.code in codes:
            continue
        cand = by_code.get(s.code, {})
        row = {
            "股票代码": s.code,
            "股票名称": s.name,
            "战法": STRATEGY_NAME,
            "评分": round(s.total, 2),
            "加入自选原因": _watchlist_add_reason(s, cand),
        }
        pkyd_tags = stock_pkyd_tags(cand)
        if pkyd_tags:
            row["盘口异动标签"] = pkyd_tags
        added.append(row)
        codes.add(s.code)

    if added:
        merged = existing + added
        save_optional(
            merged,
            delta={"added": added, "removed": []},
        )

    save_derived("scores_watchlist.json", [s.to_dict() for s in scores])
    save_derived("optional_delta.json", {"added": added, "removed": []})

    section_lines = ["九、自选更新", ""]
    if added:
        section_lines.append("【新增自选】")
        for r in added:
            section_lines.append(f"· {r['股票名称']}（{r['股票代码']}）评分{r['评分']} [{r['战法']}]")
    else:
        section_lines.append("【新增自选】")
        section_lines.append("本轮无新增；候选评分摘要：")
        section_lines.extend(_score_summary_lines(scores) or ["· 无候选数据"])
    return added, "\n".join(section_lines), scores


def process_news(raw: dict, timestamp: str) -> str:
    payload = unwrap_payload(raw)
    news_list = payload if isinstance(payload, list) else [payload]
    user = json.dumps({"news": news_list}, ensure_ascii=False)[:140000]
    summary = call_llm(prompt_news(), user, max_tokens=4000)
    if "综合解读" in summary:
        tail = summary.split("综合解读", 1)[-1]
        write_news_summary(f"综合解读{tail.strip()[:800]}")
    return summary


def process_pre_market(raw: dict) -> str:
    payload = _prepare_payload(raw)
    ctx = ScoreContext.from_payload(payload, mode="pre_market")

    raw_buy, raw_sell, executable, audit = generate_confirmed_signals(ctx, mode="pre_market")
    executed = execute_signals(executable)
    brief = build_engine_brief(ctx, payload, mode="pre_market")

    narrative = call_llm(
        prompt_pre_market(),
        build_user_msg(payload, mode="pre_market", engine_brief=brief),
        max_tokens=8000,
    )
    no_trade = "" if executed else _no_trade_reason("pre_market", ctx, len(raw_buy), 0)
    ops = _build_operation_section(executed, section="四、操作", no_trade_detail=no_trade)
    save_derived(
        "signals.json",
        {
            "raw_buy": [asdict(s) for s in raw_buy],
            "raw_sell": [],
            "executable": [asdict(s) for s in executable],
            "confirmation_audit": audit,
        },
    )
    return narrative.rstrip() + "\n\n" + ops


def process_during_market(raw: dict) -> str:
    payload = _prepare_payload(raw)
    ctx = ScoreContext.from_payload(payload, mode="during_market")

    raw_buy, raw_sell, executable, audit = generate_confirmed_signals(ctx, mode="during_market")
    executed = execute_signals(executable)

    engine = ScoringEngine()
    holding_scores = engine.score_many(ctx, payload.get("持仓股") or get_optional())
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

    narrative = call_llm(
        prompt_during_market(),
        build_user_msg(
            payload,
            mode="during_market",
            engine_brief=build_engine_brief(ctx, payload, mode="during_market"),
        ),
        max_tokens=7000,
    )
    no_trade = "" if executed else _no_trade_reason(
        "during_market", ctx, len(raw_buy), len(raw_sell)
    )
    ops = _build_operation_section(executed, section="五、操作", no_trade_detail=no_trade)
    return narrative.rstrip() + "\n\n" + ops


def process_lunch_review(raw: dict) -> str:
    payload = _prepare_payload(raw)
    ctx = ScoreContext.from_payload(payload, mode="post_market_lunch")

    brief = build_engine_brief(ctx, payload, mode="post_market_lunch")
    narrative = call_llm(
        prompt_lunch_review(),
        build_user_msg(payload, mode="post_market_lunch", engine_brief=brief),
        max_tokens=8000,
        temperature=0.1,
    )
    return narrative.rstrip()


def process_evening_review(raw: dict) -> str:
    payload = _prepare_payload(raw)
    ctx = ScoreContext.from_payload(payload, mode="post_market_evening")

    added, optional_section, scores = _update_watchlist_evening(ctx)
    brief = build_engine_brief(
        ctx,
        payload,
        mode="post_market_evening",
        watchlist_scores=scores,
        watchlist_added=added,
    )
    narrative = call_llm(
        prompt_evening_review(),
        build_user_msg(payload, mode="post_market_evening", engine_brief=brief),
        max_tokens=9000,
        temperature=0.1,
    )
    return narrative.rstrip() + "\n\n" + optional_section


def pipeline_allowed_for_mode(mode: str, *, on: date | None = None) -> bool:
    """是否应运行该模式的完整流水线（与 FastAPI 定时任务一致，基于 `is_real_workday_cn`）。

    - 新闻：始终允许。
    - 盘前/盘中/午间：仅当日为大陆真实工作日。
    - 晚间复盘：当日为工作日，或「当日非工作日但次日为工作日」（节假日前夜备盘口径）。
    """
    if mode == "news":
        return True
    d = on if on is not None else datetime.now().date()
    if mode == "post_market_evening":
        return is_real_workday_cn(d) or is_real_workday_cn(d + timedelta(days=1))
    return is_real_workday_cn(d)


def run_mode(mode: str, timestamp: str) -> None:
    """单次运行完整流水线：fetch → process → save → feishu。"""
    settings = get_settings()
    label = _MODE_LABELS.get(mode, mode)

    if not settings.QUANT_TEST_PHASE and not pipeline_allowed_for_mode(mode):
        print("当前日期/模式不满足交易日历条件，跳过")
        return

    try:
        raw = fetch_mode(mode)
        if settings.QUANT_USE_LOCAL_FIXTURE:
            print(f"本地数据：已加载 {fixture_path_for_mode(mode)}")
        else:
            print("数据拉取成功（HTTP API）")
    except Exception as e:
        print(f"数据拉取失败: {e}")
        sys.exit(1)

    payload = _prepare_payload(raw)
    save_raw(mode, payload)

    try:
        if mode == "news":
            body = process_news(raw, timestamp)
        elif mode == "pre_market":
            body = process_pre_market(raw)
        elif mode == "during_market":
            body = process_during_market(raw)
        elif mode == "post_market_lunch":
            body = process_lunch_review(raw)
        elif mode == "post_market_evening":
            body = process_evening_review(raw)
        else:
            print(f"未知模式: {mode}")
            sys.exit(1)
        print("分析完成")
    except Exception as e:
        print(f"分析失败: {e}")
        body = f"服务异常，请稍后重试。({e})"

    message = format_push_message(label, timestamp, body)
    save_review(mode, message)

    if mode == "post_market_evening":
        try:
            lesson = call_llm(
                "提取本次复盘中的1-3条可执行经验教训，80字以内，纯文本。",
                body[-3000:],
                max_tokens=300,
            )
            append_lesson(lesson.strip())
        except Exception as e:
            print(f"经验提炼失败: {e}")

    try:
        token = get_token()
        send_msg(message, token)
        print("飞书推送成功")
    except Exception as e:
        print(f"飞书推送失败: {e}")

    print("\n" + "=" * 60)
    print(message[:2000] if len(message) > 2000 else message)
    print("=" * 60)
