"""分析编排：各模式的核心调用逻辑 + 程序入口。"""

import json
import os
import sys
from datetime import datetime

import requests

from quant.config import DATA_DIR, NEWS_IMPACT_SUMMARY_FILE
from quant.data_fetch import fetch_during_market, fetch_news, fetch_post_market, fetch_pre_market
from quant.data_filter import filter_payload
from quant.data_io import (
    archive_optional,
    compute_holdings_market_value,
    extract_and_save_memory, get_cash, get_optional, read_recent_stoploss,
    read_user_text, save_optional, sum_today_realized_pnl, tail_during_market,
    tail_evening_review,
    tail_fund_only, tail_lunch_review, unwrap_payload,
    update_popularity_history, read_popularity_summary,
)
from quant.feishu import get_token, send_msg
from quant.llm import call_llm
from quant.post_process import parse_and_update, replace_json_for_feishu, save_raw_data, save_review
from quant.prompts import (
    build_user_msg, prompt_during_narrative,
    prompt_evening_narrative, prompt_lunch_narrative,
    prompt_news_system, prompt_pre_market,
)
from quant.push_format import format_push_message
from quant.rules.base import ChainResult
from quant.rules.context import RuleContext
from quant.rules.registry import get_chains_for_mode, run_global_check, run_stock_chain
from quant.signals import generate_buy_signals, generate_sell_signals
from quant.trade_executor import ExecutedTrade, execute_signals


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

_NEWS_SUMMARY_COMPRESS_CHARS = 600


def _extract_news_brief(summary: str) -> str:
    for keyword in ("综合解读：", "综合解读:", "综合解读\n"):
        idx = summary.find(keyword)
        if idx != -1:
            return summary[idx:].strip()
    paragraphs = [p.strip() for p in summary.split("\n\n") if p.strip()]
    if paragraphs:
        last = paragraphs[-1]
        if not last[:2].replace(".", "").replace("、", "").isdigit():
            return last
    return summary[-300:].strip()


def _append_and_compress_news_brief(new_brief: str) -> None:
    time_tag = datetime.now().strftime("%H:%M")
    entry = f"[{time_tag}] {new_brief}"
    existing = ""
    if os.path.isfile(NEWS_IMPACT_SUMMARY_FILE):
        try:
            existing = read_user_text(NEWS_IMPACT_SUMMARY_FILE).strip()
        except OSError:
            existing = ""
    if not existing:
        combined = entry
    else:
        combined = existing + "\n" + entry
    if len(combined) > _NEWS_SUMMARY_COMPRESS_CHARS:
        try:
            compressed = call_llm(
                "你是A股短线交易新闻研判助手。请将以下多批次新闻综合解读合并精炼为一段话，"
                "保留所有关键信息（政策方向、利好/利空板块、情绪判断、操作建议），去除重复，"
                "200字以内，纯文本输出。",
                combined,
                max_tokens=500,
            )
            combined = f"综合解读（截至{time_tag}）：{compressed.strip()}"
        except Exception:
            combined = combined[-_NEWS_SUMMARY_COMPRESS_CHARS:]
    with open(NEWS_IMPACT_SUMMARY_FILE, "w", encoding="utf-8") as f:
        f.write(combined)


def process_news(raw_data: dict, timestamp: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    time_str = datetime.now().strftime("%H_%M_%S")
    os.makedirs(f"{DATA_DIR}/news/{today}", exist_ok=True)
    news_file = f"{DATA_DIR}/news/{today}/{time_str}.md"
    user = f"原始新闻：\n{json.dumps(raw_data, ensure_ascii=False)[:140000]}"
    summary = call_llm(prompt_news_system(), user, max_tokens=4000)
    with open(news_file.replace('.md', '-origin.json'), "w", encoding="utf-8") as f:
        json.dump(raw_data, f, ensure_ascii=False, indent=2)
    with open(news_file, "w", encoding="utf-8") as f:
        f.write(f"# 新闻 {timestamp}\n\n{summary}\n")
    brief = _extract_news_brief(summary)
    _append_and_compress_news_brief(brief)
    return summary


# ---------------------------------------------------------------------------
# Rule engine integration
# ---------------------------------------------------------------------------

def _build_rule_context(payload: dict, mode: str) -> RuleContext:
    """从 payload 构建规则引擎上下文。"""
    holdings_live = payload.get("持仓股", [])
    # 总权益 = 磁盘现金（成交唯一写入源）+ 本帧 payload 持仓按盘口估算市值，避免盘中仍用过期快照
    fund_live = get_cash() + compute_holdings_market_value(holdings_live)
    today_str = datetime.now().strftime("%Y-%m-%d")
    daily_realized = sum_today_realized_pnl(today_str)
    return RuleContext(
        market_state=payload.get("市场状态机", {}),
        index_data=payload.get("大盘指数", {}),
        capital_flow=payload.get("大盘资金流", []),
        limit_up_stats=payload.get("涨停统计", []),
        popularity_list=payload.get("同花顺人气榜", []),
        concept_sectors=payload.get("概念板块", {}),
        watchlist=payload.get("自选股", []),
        holdings=holdings_live,
        fund=fund_live,
        stoploss_records=read_recent_stoploss(),
        profit_effect=payload.get("赚钱效应", {}),
        daily_pnl=daily_realized,
    )


def _run_rules_for_mode(payload: dict, mode: str) -> str:
    """运行规则引擎，返回规则检查摘要（注入 LLM prompt）。"""
    try:
        ctx = _build_rule_context(payload, mode)
        parts = [run_global_check(ctx)]
        chains = get_chains_for_mode(mode)

        # 对自选股中的每只标的运行对应的规则链
        if mode in ("post_market_lunch", "post_market_evening"):
            # 复盘模式：加自选链的结果只给 optional LLM，不给 narrative
            # 此处只返回全局检查结果
            pass
        elif mode in ("pre_market", "during_market"):
            # 对自选股运行买入链
            buy_chain_key = "zt_buy_pre" if mode == "pre_market" else "zt_buy_intraday"
            zt_buy = chains.get(buy_chain_key)
            lht_buy = chains.get("lht_buy")
            zsll_buy = chains.get("zsll_buy")
            for stock in ctx.watchlist:
                strategy = str(stock.get("战法", "")).strip()
                if "涨停" in strategy and zt_buy:
                    parts.append(run_stock_chain(zt_buy, ctx, stock))
                elif "主升浪" in strategy and zsll_buy:
                    parts.append(run_stock_chain(zsll_buy, ctx, stock))
                elif "龙回头" in strategy and lht_buy:
                    parts.append(run_stock_chain(lht_buy, ctx, stock))

            # 对持仓股运行持仓/卖出链
            zt_hold = chains.get("zt_hold")
            zt_sell = chains.get("zt_sell")
            lht_hold = chains.get("lht_hold")
            lht_sell = chains.get("lht_sell")
            zsll_hold = chains.get("zsll_hold")
            zsll_sell = chains.get("zsll_sell")
            for stock in ctx.holdings:
                strategy = str(stock.get("战法", stock.get("买入原因", ""))).strip()
                if "涨停" in strategy:
                    if zt_hold:
                        parts.append(run_stock_chain(zt_hold, ctx, stock))
                    if zt_sell:
                        parts.append(run_stock_chain(zt_sell, ctx, stock))
                elif "主升浪" in strategy:
                    if zsll_hold:
                        parts.append(run_stock_chain(zsll_hold, ctx, stock))
                    if zsll_sell:
                        parts.append(run_stock_chain(zsll_sell, ctx, stock))
                elif "龙回头" in strategy:
                    if lht_hold:
                        parts.append(run_stock_chain(lht_hold, ctx, stock))
                    if lht_sell:
                        parts.append(run_stock_chain(lht_sell, ctx, stock))

        summary = "\n".join(p for p in parts if p)
        return f"\n\n【规则引擎预检】\n{summary}" if summary else ""
    except Exception as e:
        print(f"规则引擎执行异常（不影响主流程）: {e}")
        return ""


def _chain_failure_text(chain_result: ChainResult | None) -> str:
    """规则链失败原因单行拼接。"""
    if chain_result is None:
        return "（未执行）"
    parts = [r.reason for r in chain_result.failures if r.reason]
    return "; ".join(parts) if parts else "（无失败明细）"


def _prior_chain_caption(chain_result: ChainResult | None, chain_enabled: bool) -> str:
    """互斥说明里描述「上一优先级」未入选原因；链未启用时单独标注。"""
    if not chain_enabled:
        return "该战法链未启用"
    return _chain_failure_text(chain_result)


def _run_rules_for_optional(payload: dict, mode: str) -> dict:
    """运行加自选规则链，直接决策哪些标的加入自选。

    复盘时单票至多归入一种战法：**涨停板战法 > 龙回头战法 > 主升浪战法**（先通过者优先，
    后续战法链不再参评该股）。

    Returns:
        {
            "added_zt": [{"股票代码", "股票名称", "战法", "加入自选原因"}...],
            "added_lht": [...],
            "added_zsll": [...],
            "rejected_zt": [{"股票名称", "股票代码", "reason"}...],
            "rejected_lht": [...],
            "rejected_zsll": [...],
            "exclusivity_decisions": [
                {"股票代码", "股票名称", "入选战法", "互斥说明"},
                ...
            ],
            "summary": str,  # 人类可读摘要
        }
    """
    result = {
        "added_zt": [],
        "added_lht": [],
        "added_zsll": [],
        "rejected_zt": [],
        "rejected_lht": [],
        "rejected_zsll": [],
        "exclusivity_decisions": [],
        "summary": "",
    }
    try:
        ctx = _build_rule_context(payload, mode)
        chains = get_chains_for_mode(mode)
        zt_chain = chains.get("zt_watchlist")
        lht_chain = chains.get("lht_watchlist")
        zsll_chain = chains.get("zsll_watchlist")

        for stock in ctx.popularity_list[:20]:
            code = str(stock.get("股票代码", "")).strip()
            name = str(stock.get("股票名称", "")).strip()
            if not code:
                continue

            ctx.target_stock = stock

            zt_res: ChainResult | None = None
            lht_res: ChainResult | None = None
            zsll_res: ChainResult | None = None

            if zt_chain:
                zt_res = zt_chain.evaluate(ctx)
                if zt_res.all_passed:
                    zt_reasons = "; ".join(r.reason for r in zt_res.passes if r.reason)
                    result["added_zt"].append({
                        "股票代码": code,
                        "股票名称": name,
                        "战法": "涨停板战法",
                        "加入自选原因": f"【涨停板战法】{zt_reasons}",
                    })
                    result["exclusivity_decisions"].append({
                        "股票代码": code,
                        "股票名称": name,
                        "入选战法": "涨停板战法",
                        "互斥说明": (
                            "涨停板战法链全通过（互斥优先级第1），该股不再参与龙回头/主升浪评选。"
                        ),
                    })
                    continue
                result["rejected_zt"].append({
                    "股票代码": code,
                    "股票名称": name,
                    "reason": _chain_failure_text(zt_res),
                })

            if lht_chain:
                lht_res = lht_chain.evaluate(ctx)
                if lht_res.all_passed:
                    lht_reasons = "; ".join(r.reason for r in lht_res.passes if r.reason)
                    result["added_lht"].append({
                        "股票代码": code,
                        "股票名称": name,
                        "战法": "龙回头战法",
                        "加入自选原因": f"【龙回头战法】{lht_reasons}",
                    })
                    zt_txt = _prior_chain_caption(zt_res, bool(zt_chain))
                    result["exclusivity_decisions"].append({
                        "股票代码": code,
                        "股票名称": name,
                        "入选战法": "龙回头战法",
                        "互斥说明": (
                            f"涨停板战法未通过（{zt_txt}）；龙回头战法链全通过（互斥优先级第2），"
                            "该股不再参评主升浪。"
                        ),
                    })
                    continue
                result["rejected_lht"].append({
                    "股票代码": code,
                    "股票名称": name,
                    "reason": _chain_failure_text(lht_res),
                })

            if zsll_chain:
                zsll_res = zsll_chain.evaluate(ctx)
                if zsll_res.all_passed:
                    zsll_reasons = "; ".join(r.reason for r in zsll_res.passes if r.reason)
                    result["added_zsll"].append({
                        "股票代码": code,
                        "股票名称": name,
                        "战法": "主升浪战法",
                        "加入自选原因": f"【主升浪战法】{zsll_reasons}",
                    })
                    zt_txt = _prior_chain_caption(zt_res, bool(zt_chain))
                    lht_txt = _prior_chain_caption(lht_res, bool(lht_chain))
                    result["exclusivity_decisions"].append({
                        "股票代码": code,
                        "股票名称": name,
                        "入选战法": "主升浪战法",
                        "互斥说明": (
                            f"涨停板战法未通过（{zt_txt}）；龙回头战法未通过（{lht_txt}）；"
                            "主升浪战法链全通过（互斥优先级第3）。"
                        ),
                    })
                    continue
                result["rejected_zsll"].append({
                    "股票代码": code,
                    "股票名称": name,
                    "reason": _chain_failure_text(zsll_res),
                })

            zt_txt = _prior_chain_caption(zt_res, bool(zt_chain))
            lht_txt = _prior_chain_caption(lht_res, bool(lht_chain))
            zsll_txt = _prior_chain_caption(zsll_res, bool(zsll_chain))
            result["exclusivity_decisions"].append({
                "股票代码": code,
                "股票名称": name,
                "入选战法": "",
                "互斥说明": (
                    f"三板战法均未入选。涨停板：{zt_txt}；龙回头：{lht_txt}；主升浪：{zsll_txt}"
                ),
            })

        # 生成摘要
        parts = []
        decs = result.get("exclusivity_decisions") or []
        if decs:
            parts.append(
                "【复盘·战法互斥】人气榜前20逐只判定，优先级：涨停板战法 > 龙回头战法 > 主升浪战法，"
                "至多归入其一。"
            )
            for d in decs:
                tag = d.get("入选战法") or "未入选"
                parts.append(
                    f"  · {d.get('股票名称', '')}({d.get('股票代码', '')}) [{tag}] "
                    f"{d.get('互斥说明', '')}"
                )
            parts.append("")

        if result["added_zt"]:
            parts.append(f"涨停板战法新增自选{len(result['added_zt'])}只：" +
                         "、".join(f"{s['股票名称']}({s['股票代码']})" for s in result["added_zt"]))
        else:
            top_rejects = result["rejected_zt"][:3]
            if top_rejects:
                parts.append("涨停板战法自选未更新原因：" +
                             "；".join(f"{r['股票名称']}({r['股票代码']}){r['reason']}" for r in top_rejects))
            else:
                parts.append("涨停板战法自选未更新原因：人气榜无数据")

        if result["added_lht"]:
            parts.append(f"龙回头战法新增自选{len(result['added_lht'])}只：" +
                         "、".join(f"{s['股票名称']}({s['股票代码']})" for s in result["added_lht"]))
        else:
            top_rejects = result["rejected_lht"][:3]
            if top_rejects:
                parts.append("龙回头战法自选未更新原因：" +
                             "；".join(f"{r['股票名称']}({r['股票代码']}){r['reason']}" for r in top_rejects))
            else:
                parts.append("龙回头战法自选未更新原因：人气榜无数据")

        if result["added_zsll"]:
            parts.append(f"主升浪战法新增自选{len(result['added_zsll'])}只：" +
                         "、".join(f"{s['股票名称']}({s['股票代码']})" for s in result["added_zsll"]))
        else:
            top_rejects = result["rejected_zsll"][:3]
            if top_rejects:
                parts.append("主升浪战法自选未更新原因：" +
                             "；".join(f"{r['股票名称']}({r['股票代码']}){r['reason']}" for r in top_rejects))
            else:
                parts.append("主升浪战法自选未更新原因：人气榜无数据")

        result["summary"] = "\n".join(parts)
    except Exception as e:
        print(f"规则引擎(加自选)执行异常: {e}")
        result["summary"] = f"规则引擎(加自选)异常: {e}"

    return result


# ---------------------------------------------------------------------------
# Pre-market
# ---------------------------------------------------------------------------

def analyze_pre_market(raw_data: dict, timestamp: str) -> str:
    payload = unwrap_payload(raw_data)
    ctx = _build_rule_context(payload, "pre_market")
    chains = get_chains_for_mode("pre_market")

    # 1. 全局预检
    global_chain = chains.get("global")
    global_summary = ""
    if global_chain:
        global_result = global_chain.evaluate(ctx)
        global_summary = global_result.summary()

    # 2. 仅生成涨停板战法买入信号（盘前不操作龙回头/主升浪）
    zt_only_chains = {k: v for k, v in chains.items() if k not in ("lht_buy", "zsll_buy")}
    buy_signals = generate_buy_signals(ctx, zt_only_chains)

    # 3. 原子执行交易
    executed = execute_signals(buy_signals)
    if executed:
        print(f"盘前执行交易 {len(executed)} 笔")

    # 4. LLM 叙述（一~三）
    rules_tail = f"\n\n【规则引擎预检】\n{global_summary}" if global_summary else ""
    tail = tail_fund_only() + rules_tail
    u = build_user_msg(filter_payload(payload, "pre_market"), tail=tail)
    narrative = call_llm(prompt_pre_market(), u, max_tokens=8000, temperature=0.16)

    # 5. 拼接第四节（规则引擎产出）
    section_four = _build_operation_section(executed, section="四、操作")

    return narrative.rstrip() + "\n\n" + section_four


# ---------------------------------------------------------------------------
# During market
# ---------------------------------------------------------------------------

def analyze_during_market(raw_data: dict, timestamp: str) -> str:
    payload = unwrap_payload(raw_data)
    ctx = _build_rule_context(payload, "during_market")
    chains = get_chains_for_mode("during_market")

    # 1. 全局预检
    global_chain = chains.get("global")
    global_summary = ""
    if global_chain:
        global_result = global_chain.evaluate(ctx)
        global_summary = global_result.summary()

    # 2. 规则引擎生成买卖信号
    buy_signals = generate_buy_signals(ctx, chains)
    sell_signals = generate_sell_signals(ctx, chains)
    all_signals = sell_signals + buy_signals  # 卖出优先

    # 3. 原子执行交易（更新持仓/资金/操作记录）
    executed = execute_signals(all_signals)
    if executed:
        print(f"盘中执行交易 {len(executed)} 笔")

    # 4. LLM 叙述（一~四，不做任何买卖决策）
    rules_tail = f"\n\n【规则引擎预检】\n{global_summary}" if global_summary else ""
    tail = tail_during_market() + rules_tail
    u_narrative = build_user_msg(
        filter_payload(payload, "during_narrative"), tail=tail,
    )
    narrative = call_llm(prompt_during_narrative(), u_narrative, max_tokens=6000, temperature=0.16)

    # 5. 拼接第五节（纯规则引擎产出）
    section_five = _build_operation_section(executed)

    return narrative.rstrip() + "\n\n" + section_five


def _build_operation_section(executed: list[ExecutedTrade], *, section: str = "五、操作") -> str:
    """从已执行交易列表构建操作段落。"""
    lines = [section]
    if not executed:
        lines.append("本轮无操作信号。")
        return "\n".join(lines)
    for e in executed:
        s = e.signal
        pnl_part = f"，盈亏：{e.pnl:+.2f}元" if e.pnl else ""
        lines.append(
            f"· {s.action}{s.name}（{s.code}），"
            f"时间：{e.timestamp}，"
            f"价格：{s.price:.2f}，"
            f"数量：{s.quantity // 100}手，"
            f"战法：{s.strategy}，"
            f"理由：{s.reason}{pnl_part}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Review (lunch / evening)
# ---------------------------------------------------------------------------

def _run_review(raw_data: dict, tail: str, *, lunch: bool) -> str:
    payload = unwrap_payload(raw_data)
    hot = payload.get("同花顺人气榜", [])
    if hot:
        update_popularity_history(hot)
    pop_summary = read_popularity_summary()
    pop_tail = f"\n\n【上榜跟踪】{pop_summary}" if pop_summary else ""
    mode = "post_market_lunch" if lunch else "post_market_evening"

    # 1. 全局规则预检（市场状态/熔断等）—— 给 narrative LLM 参考
    rules_summary = _run_rules_for_mode(payload, mode)

    # 2. 规则引擎直接决策加自选（不经过 LLM）
    optional_result = _run_rules_for_optional(payload, mode)
    new_optional = (
        optional_result["added_zt"]
        + optional_result["added_lht"]
        + optional_result["added_zsll"]
    )

    # 3. 更新自选文件：追加新增标的（去重）
    existing = get_optional()
    existing_codes = {str(r.get("股票代码", "")).strip() for r in existing}
    added_rows = []
    for row in new_optional:
        if row["股票代码"] not in existing_codes:
            added_rows.append(row)
            existing_codes.add(row["股票代码"])
    if added_rows:
        merged = existing + added_rows
        archive_optional(merged, old_list=existing)
        save_optional(merged)
        print(f"规则引擎新增自选 {len(added_rows)} 只: "
              + ", ".join(f"{r['股票名称']}({r['股票代码']})" for r in added_rows))

    # 4. 构建自选更新段落（纯规则引擎产出，无 LLM 参与）
    section_num = "六" if lunch else "九"
    optional_section = _build_optional_section(optional_result, section_num=section_num)

    # 5. LLM 仅负责 narrative（市场回顾/自选表现/持仓表现/经验总结）
    #    注意：不注入本次自选决策摘要，避免 LLM 在"自选股全天表现"中混入新增标的
    #    "自选股全天表现"只关注 payload 中已有的自选股（T-1日加入的）
    u_narrative = build_user_msg(
        filter_payload(payload, "narrative"),
        tail=tail + rules_summary + pop_tail,
    )
    sys_n = prompt_lunch_narrative() if lunch else prompt_evening_narrative()
    narrative = call_llm(sys_n, u_narrative, max_tokens=8000, temperature=0.1)

    return narrative.rstrip() + "\n\n" + optional_section


def _build_optional_section(optional_result: dict, *, section_num: str = "九") -> str:
    """从规则引擎结果构建自选更新段落（可读列表格式）。"""
    lines = [f"{section_num}、自选更新"]
    merged = (
        optional_result["added_zt"]
        + optional_result["added_lht"]
        + optional_result["added_zsll"]
    )

    if merged:
        for i, row in enumerate(merged, 1):
            tag = row.get("战法", "")
            reason = row.get("加入自选原因", "")
            lines.append(f"{i}、{row['股票名称']}({row['股票代码']}) [{tag}] {reason}")
    else:
        lines.append("本次无新增自选标的。")

    decs = optional_result.get("exclusivity_decisions") or []
    if decs:
        lines.append("")
        lines.append(
            "战法互斥判断（人气榜扫描范围内逐只至多归入一种战法；优先级："
            "涨停板战法 > 龙回头战法 > 主升浪战法）："
        )
        for d in decs:
            tag = d.get("入选战法") or "未入选"
            lines.append(
                f"  · {d.get('股票名称', '')}({d.get('股票代码', '')}) [{tag}] "
                f"{d.get('互斥说明', '')}"
            )

    # 追加未更新原因
    if not optional_result["added_zt"]:
        rejects = optional_result["rejected_zt"][:5]
        if rejects:
            lines.append("")
            lines.append("涨停板战法未入选原因：")
            for r in rejects:
                lines.append(f"  · {r['股票名称']}({r['股票代码']})：{r['reason']}")

    if not optional_result["added_lht"]:
        rejects = optional_result["rejected_lht"][:5]
        if rejects:
            lines.append("")
            lines.append("龙回头战法未入选原因：")
            for r in rejects:
                lines.append(f"  · {r['股票名称']}({r['股票代码']})：{r['reason']}")

    if not optional_result["added_zsll"]:
        rejects = optional_result["rejected_zsll"][:5]
        if rejects:
            lines.append("")
            lines.append("主升浪战法未入选原因：")
            for r in rejects:
                lines.append(f"  · {r['股票名称']}({r['股票代码']})：{r['reason']}")

    return "\n".join(lines)


def analyze_lunch_market(raw_data: dict, timestamp: str) -> str:
    return _run_review(raw_data, tail_lunch_review(), lunch=True)


def analyze_evening_market(raw_data: dict, timestamp: str) -> str:
    return _run_review(raw_data, tail_evening_review(), lunch=False)


# ---------------------------------------------------------------------------
# Trading day check
# ---------------------------------------------------------------------------

def is_trading_day() -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        resp = requests.get(f"https://timor.tech/api/holiday/info/{today}", timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0:
            t = result.get("type", {})
            t_type = t.get("type") if isinstance(t, dict) else None
            return t_type in [0, 2]
        return True
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_mode(mode: str, timestamp: str):
    """执行指定模式的完整流程：拉数据 → 分析 → 后处理 → 推送飞书。"""

    labels = {
        "news": "新闻聚焦",
        "pre_market": "盘前分析",
        "during_market": "盘中实时",
        "post_market_lunch": "午间复盘",
        "post_market_evening": "晚间复盘",
    }
    label = labels.get(mode, mode)

    if mode != "news" and not is_trading_day():
        print("今日非交易日，跳过")

    fetch_map = {
        "news": fetch_news,
        "pre_market": fetch_pre_market,
        "during_market": fetch_during_market,
        "post_market_lunch": fetch_post_market,
        "post_market_evening": fetch_post_market,
    }
    if mode not in fetch_map:
        print(f"未知模式: {mode}")
        sys.exit(1)

    try:
        data = fetch_map[mode]()
        print("数据拉取成功")
    except Exception as e:
        print(f"数据拉取失败: {e}")
        sys.exit(1)

    analysis = ""
    try:
        if mode == "news":
            summary = process_news(data, timestamp)
            analysis = format_push_message("新闻聚焦", timestamp, summary, mode)
            save_raw_data(mode, data)
        elif mode == "pre_market":
            analysis = format_push_message("盘前分析", timestamp, analyze_pre_market(data, timestamp), mode)
            save_raw_data(mode, data)
        elif mode == "during_market":
            analysis = format_push_message("盘中实时", timestamp, analyze_during_market(data, timestamp), mode)
            save_raw_data(mode, data)
        elif mode == "post_market_lunch":
            analysis = format_push_message("午间复盘", timestamp, analyze_lunch_market(data, timestamp), mode)
            save_review(timestamp, analysis, mode, data)
            extract_and_save_memory(analysis, lunch=True)
        elif mode == "post_market_evening":
            analysis = format_push_message("晚间复盘", timestamp, analyze_evening_market(data, timestamp), mode)
            save_review(timestamp, analysis, mode, data)
            extract_and_save_memory(analysis, lunch=False)
        print("分析完成")
    except Exception as e:
        print(f"分析失败: {e}")
        analysis = f"【{label}】{timestamp}\n\n服务异常，请稍后重试。"

    feishu_content = analysis
    if mode in ("during_market", "pre_market"):
        # 盘中/盘前模式：交易已在 analyze 内部由规则引擎完成，
        # 无需 parse_and_update() 解析 LLM 输出中的 JSON
        pass
    else:
        try:
            # 仅替换飞书展示用 JSON 块为磁盘真实持仓/自选；不写资金与成交文件
            pu = parse_and_update(
                analysis,
                mode,
                market_payload=unwrap_payload(data)
                if mode in ("post_market_lunch", "post_market_evening")
                else None,
            )
            feishu_content = replace_json_for_feishu(
                analysis,
                optional_span=pu["optional_span"],
                optional_lines=pu["optional_lines"],
                holdings_span=pu["holdings_span"],
                holdings_lines=pu["holdings_lines"],
            )
        except Exception as e:
            print(f"解析更新失败: {e}")

    try:
        token = get_token()
        send_msg(feishu_content, token)
        print("飞书推送成功")
    except Exception as e:
        print(f"飞书推送失败: {e}")

    print("\n" + "=" * 60)
    out_show = feishu_content
    print(out_show[:2000] if len(out_show) > 2000 else out_show)
    print("=" * 60)
