"""无成交时「四、操作」段的口语化说明（不含仓位规则）。"""

from __future__ import annotations

import random
import re
from typing import Any

from quant.config import load_gates_config
from quant.gates.rules import check_buy_gates
from quant.scoring.context import ScoreContext
from quant.scoring.engine import ScoringEngine
from quant.signals.models import TradeSignal
from quant.store.state import get_holdings
from quant.strategy.intraday import intraday_allows_buy
from quant.strategy.main_wave import detect_buy_setup
from quant.strategy.trend import trend_allows_buy


def _simplify_reason(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(r"^\[[^\]]+\]", "", s)
    s = re.sub(r"评分\d+(?:\.\d+)?[；;]?", "", s)
    s = re.sub(r"三确认完成[^；;]*[；;]?", "", s)
    s = s.strip("；; ")

    rules: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"现价[\d.]+低于分时均价[\d.]+"), "股价还在均价下方，接出没时机"),
        (re.compile(r"近\d+分钟走势下行"), "分时走弱，买点不成立"),
        (re.compile(r"近\d+分钟跌多涨少"), "分时跌多涨少，先观望"),
        (re.compile(r"分时资金未见改善"), "分时承接一般"),
        (re.compile(r"距日内高点回撤[\d.]+%过大"), "冲高回落明显，不接"),
        (re.compile(r"当日涨幅[\d.-]+%偏弱"), "日内走势偏弱"),
        (re.compile(r"波段内未触发买点"), "趋势在但买点还没出来"),
        (re.compile(r"未满足主升趋势"), "还没走成主升形态"),
        (re.compile(r"趋势阶段\[[^\]]+\][：:]?"), ""),
        (re.compile(r"主升波段上升途中"), "上升途中，等确认"),
        (re.compile(r"主升波段回调至均线区企稳"), "回调企稳，等确认"),
    ]
    for pat, repl in rules:
        s = pat.sub(repl, s)
    s = re.sub(r"[；;]{2,}", "；", s).strip("；; ")
    return s[:80] if s else ""


def _audit_line(row: dict) -> str | None:
    if row.get("可执行"):
        return None
    name = str(row.get("股票名称") or row.get("股票代码") or "").strip()
    if not name:
        return None
    status = str(row.get("状态") or "")
    action = str(row.get("方向") or "")
    is_sell = action == "卖出"
    if is_sell and ("等待14:30" in status or "第二次确认" in status):
        return f"{name}：卖点已二次确认，等尾盘最终确认"
    if is_sell and "确认" in status:
        return f"{name}：出现卖点信号，确认次数还不够"
    if not is_sell and ("等待14:30" in status or "第二次确认" in status):
        return f"{name}：买点已二次确认，等尾盘最终确认"
    if not is_sell and "确认" in status:
        return f"{name}：出现买点信号，确认次数还不够"
    reason = _simplify_reason(str(row.get("理由") or ""))
    return f"{name}：{reason}" if reason else None


def _audit_examples_by_side(
    audit: list[dict],
    *,
    limit: int = 2,
) -> tuple[list[str], list[str]]:
    """三确认审计：买入归自选、卖出归持仓，各自随机抽样。"""
    buy_pool: list[str] = []
    sell_pool: list[str] = []
    for row in audit:
        line = _audit_line(row)
        if not line:
            continue
        if str(row.get("方向") or "") == "卖出":
            sell_pool.append(line)
        else:
            buy_pool.append(line)
    return (
        _sample_lines(buy_pool, limit=limit),
        _sample_lines(sell_pool, limit=limit),
    )


def _skip_reason_for_watchlist_stock(
    stock: dict,
    ctx: ScoreContext,
    *,
    mode: str,
    engine: ScoringEngine,
    threshold: float,
    mw_cfg: dict[str, Any],
    buy_cfg: dict[str, Any],
) -> str | None:
    """单只自选股未满足买入条件的原因；已全部通过则返回 None。"""
    code = str(stock.get("股票代码", "")).strip()
    name = str(stock.get("股票名称", "")).strip() or code
    if not code:
        return None

    gate = check_buy_gates(stock, ctx)
    if not gate.passed:
        fail = next((r for r in gate.results if not r.passed), None)
        label = (fail.reason if fail and fail.reason else "暂不符合买入条件").strip()
        if fail and fail.name == "止损冷却":
            label = "刚止损不久，冷却期内不接"
        elif fail and fail.name == "当日卖出冷却":
            label = "今日已卖过，不再回补"
        return f"{name}：{label}"

    ok_trend, trend_note = trend_allows_buy(stock, mw_cfg)
    if not ok_trend:
        note = _simplify_reason(trend_note) or "趋势还没走顺"
        return f"{name}：{note}"

    score = engine.score_stock(ctx, stock)
    if score.total < threshold:
        return f"{name}：强度还不够，再观察"

    ok, _kind, setup_reason = detect_buy_setup(stock, ctx, mw_cfg)
    if not ok:
        note = _simplify_reason(setup_reason) or "买点未出"
        return f"{name}：{note}"

    if mode == "during_market":
        ok_intra, intra_note = intraday_allows_buy(stock, buy_cfg)
        if not ok_intra:
            note = _simplify_reason(intra_note) or "分时偏弱，先不接"
            return f"{name}：{note}"

    return None


def _sample_lines(candidates: list[str], *, limit: int) -> list[str]:
    if len(candidates) <= limit:
        return candidates
    return random.sample(candidates, limit)


def _watchlist_skip_examples(ctx: ScoreContext, *, mode: str, limit: int = 2) -> list[str]:
    """仅从自选股（未持仓）抽样未满足买点的说明。"""
    mw_cfg = load_gates_config().get("main_wave") or {}
    buy_cfg = (load_gates_config().get("buy") or {}).get(
        "during_market" if mode == "during_market" else "pre_market"
    ) or {}
    engine = ScoringEngine()
    threshold = float(engine.config.get("buy_threshold", 72))
    held = {str(h.get("股票代码", "")).strip() for h in get_holdings()}

    candidates: list[str] = []
    for stock in ctx.payload.get("自选股") or []:
        if not isinstance(stock, dict):
            continue
        code = str(stock.get("股票代码", "")).strip()
        if not code or code in held:
            continue
        line = _skip_reason_for_watchlist_stock(
            stock,
            ctx,
            mode=mode,
            engine=engine,
            threshold=threshold,
            mw_cfg=mw_cfg,
            buy_cfg=buy_cfg,
        )
        if line:
            candidates.append(line)

    return _sample_lines(candidates, limit=limit)


def _format_side_parts(
    *,
    watchlist_lines: list[str],
    holding_lines: list[str],
) -> str:
    """自选 / 持仓分轨拼接，互不混用。"""
    parts: list[str] = []
    if watchlist_lines:
        parts.append("自选：" + "；".join(watchlist_lines))
    if holding_lines:
        parts.append("持仓：" + "；".join(holding_lines))
    return "。".join(parts)


def build_no_trade_note(
    ctx: ScoreContext,
    *,
    mode: str,
    raw_buy: list[TradeSignal],
    raw_sell: list[TradeSignal],
    audit: list[dict] | None = None,
) -> str:
    """口语化无成交说明：买入侧只谈自选，卖出侧只谈持仓，分轨表述。"""
    audit = audit or []

    if raw_buy or raw_sell:
        buy_ex, sell_ex = _audit_examples_by_side(audit, limit=2)
        body = _format_side_parts(watchlist_lines=buy_ex, holding_lines=sell_ex)
        if body:
            return "暂下手，" + body + "。"
        return "有标的在盯，但确认条件未齐，暂不加减仓。"

    watch_ex = _watchlist_skip_examples(ctx, mode=mode, limit=2)
    if mode == "pre_market":
        if watch_ex:
            return "盘前暂不下单。自选：" + "；".join(watch_ex) + "。"
        return "盘前暂不下单，等开盘后再看分时与买点。"
    if watch_ex:
        return "继续观望。自选：" + "；".join(watch_ex) + "。"
    return "继续观望，自选里暂无合适买点。"
