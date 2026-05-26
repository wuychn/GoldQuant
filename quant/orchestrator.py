"""五模式编排：拉数 → 评分/信号 → 落盘 → LLM 叙述 → 飞书。

模式职责
--------
news                 LLM 新闻解读，不写自选/持仓
pre_market           评分买入 + LLM 盘前叙述 + 「四、操作」
during_market        买卖信号 + LLM 盘中叙述 + 「五、操作」
post_market_lunch    LLM 午间复盘，**不更新自选**
post_market_evening  评分加自选 + LLM 晚间复盘 + 「九、自选更新」

决策与叙述分离：买卖/自选由 ScoringEngine + gates + executor 完成；
LLM 只生成正文段落，不参与下单。

ML 校准不在此模块自动运行，需手动 ``python -m quant.ml calibrate``。
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime

import requests

from quant.data_fetch import fetch_mode, unwrap_payload
from quant.execution.executor import ExecutedTrade, execute_signals
from quant.gates.rules import check_global_gates
from quant.narrative.llm import call_llm
from quant.narrative.prompts import (
    build_user_msg,
    prompt_during_market,
    prompt_evening_review,
    prompt_lunch_review,
    prompt_news,
    prompt_no_trade_reason,
    prompt_pre_market,
)
from quant.pool.builder import build_candidates
from quant.push.feishu import get_token, send_msg
from quant.push.format import format_push_message
from quant.scoring.context import ScoreContext, build_market_state
from quant.scoring.engine import ScoringEngine
from quant.signals.buy import generate_buy_signals
from quant.signals.sell import generate_sell_signals
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
    payload = unwrap_payload(raw)
    if not payload.get("市场状态机"):
        payload["市场状态机"] = build_market_state(payload)
    return payload


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
    facts = (
        f"场景：{mode}\n"
        f"市场状态：{ctx.market_state.get('状态')}\n"
        f"买入信号{buy_n}条，卖出信号{sell_n}条\n"
        f"门禁：{check_global_gates(ctx).summary()}"
    )
    try:
        text = call_llm(prompt_no_trade_reason(mode), facts, max_tokens=300, temperature=0.1)
        text = text.strip().replace("\n", " ")
        if not text.startswith("原因"):
            text = f"原因：{text}"
        return text
    except Exception as e:
        print(f"无操作原因 LLM 失败: {e}")
        return "原因：评分或门禁未通过，暂无成交。"


def _score_summary_lines(scores: list) -> list[str]:
    lines = []
    for s in sorted(scores, key=lambda x: x.total, reverse=True)[:10]:
        mark = "✓" if s.passed_threshold else "×"
        lines.append(f"· {mark} {s.name}({s.code}) {s.total:.1f}分 [{s.strategy}]")
    return lines


def _update_watchlist_evening(ctx: ScoreContext) -> tuple[list[dict], str]:
    """晚间复盘：对候选池评分，达标者 append 到 state/optional.jsonl。"""
    engine = ScoringEngine()
    candidates = build_candidates(ctx.payload)
    scores = engine.apply_threshold(engine.score_many(ctx, candidates), kind="watchlist")
    passed = [s for s in scores if s.passed_threshold]

    existing = get_optional()
    codes = {str(r.get("股票代码", "")).strip() for r in existing}
    added: list[dict] = []
    for s in passed:
        if s.code in codes:
            continue
        row = {
            "股票代码": s.code,
            "股票名称": s.name,
            "战法": s.strategy,
            "评分": round(s.total, 2),
            "加入自选原因": f"评分{s.total:.1f}达标；{s.strategy}",
        }
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
    return added, "\n".join(section_lines)


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
    save_derived("market_state.json", ctx.market_state)

    buy_signals = generate_buy_signals(ctx, mode="pre_market")
    executed = execute_signals(buy_signals)

    narrative = call_llm(
        prompt_pre_market(),
        build_user_msg(payload, extra=f"\n【评分门禁】\n{check_global_gates(ctx).summary()}"),
        max_tokens=8000,
    )
    no_trade = "" if executed else _no_trade_reason("pre_market", ctx, len(buy_signals), 0)
    ops = _build_operation_section(executed, section="四、操作", no_trade_detail=no_trade)
    save_derived("signals.json", {"buy": [asdict(s) for s in buy_signals], "sell": []})
    return narrative.rstrip() + "\n\n" + ops


def process_during_market(raw: dict) -> str:
    payload = _prepare_payload(raw)
    ctx = ScoreContext.from_payload(payload, mode="during_market")
    save_derived("market_state.json", ctx.market_state)

    buy_signals = generate_buy_signals(ctx, mode="during_market")
    sell_signals = generate_sell_signals(ctx)
    executed = execute_signals(sell_signals + buy_signals)

    engine = ScoringEngine()
    holding_scores = engine.score_many(ctx, payload.get("持仓股") or get_optional())
    save_derived("scores_holding.json", [s.to_dict() for s in holding_scores])
    save_derived(
        "signals.json",
        {"buy": [asdict(s) for s in buy_signals], "sell": [asdict(s) for s in sell_signals]},
    )

    narrative = call_llm(
        prompt_during_market(),
        build_user_msg(payload, extra=f"\n【评分门禁】\n{check_global_gates(ctx).summary()}"),
        max_tokens=7000,
    )
    no_trade = "" if executed else _no_trade_reason(
        "during_market", ctx, len(buy_signals), len(sell_signals)
    )
    ops = _build_operation_section(executed, section="五、操作", no_trade_detail=no_trade)
    return narrative.rstrip() + "\n\n" + ops


def process_lunch_review(raw: dict) -> str:
    payload = _prepare_payload(raw)
    ctx = ScoreContext.from_payload(payload, mode="post_market_lunch")
    save_derived("market_state.json", ctx.market_state)

    narrative = call_llm(
        prompt_lunch_review(),
        build_user_msg(payload, extra="\n【说明】午间复盘不更新自选股。"),
        max_tokens=8000,
        temperature=0.1,
    )
    section = "六、自选更新\n\n午间复盘不更新自选股，晚间复盘统一更新。"
    return narrative.rstrip() + "\n\n" + section


def process_evening_review(raw: dict) -> str:
    payload = _prepare_payload(raw)
    ctx = ScoreContext.from_payload(payload, mode="post_market_evening")
    save_derived("market_state.json", ctx.market_state)

    _, optional_section = _update_watchlist_evening(ctx)
    narrative = call_llm(
        prompt_evening_review(),
        build_user_msg(payload, extra="\n【说明】自选更新由评分引擎完成。"),
        max_tokens=9000,
        temperature=0.1,
    )
    return narrative.rstrip() + "\n\n" + optional_section


def is_trading_day() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        resp = requests.get(f"https://timor.tech/api/holiday/info/{today}", timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0:
            t = result.get("type", {})
            return (t.get("type") if isinstance(t, dict) else None) in (0, 2)
    except Exception:
        pass
    return True


def run_mode(mode: str, timestamp: str) -> None:
    """单次运行完整流水线：fetch → process → save → feishu。"""
    label = _MODE_LABELS.get(mode, mode)
    if mode != "news" and not is_trading_day():
        print("今日非交易日，跳过")
        return

    try:
        raw = fetch_mode(mode)
        print("数据拉取成功")
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
