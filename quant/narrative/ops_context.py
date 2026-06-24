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
from quant.store.state import get_holdings, resolve_payload_holdings
from quant.strategy.intraday import intraday_allows_buy
from quant.strategy.main_wave import detect_buy_setup
from quant.strategy.momentum import momentum_buy_floor, momentum_score
from quant.strategy.trend import trend_allows_buy


def _simplify_reason(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(r"^\[[^\]]+\]", "", s)
    s = re.sub(r"评分\d+(?:\.\d+)?[；;]?", "", s)
    s = re.sub(r"三确认完成[^；;]*[；;]?", "", s)
    s = re.sub(r"持续确认完成[^；;]*[；;]?", "", s)
    s = s.strip("；; ")

    rules: list[tuple[re.Pattern[str], str]] = [
        (re.compile(r"现价[\d.]+低于分时均价[\d.]+"), "股价还在均价下方，接出没时机"),
        (re.compile(r"近\d+分钟走势下行"), "分时走弱，买点不成立"),
        (re.compile(r"近\d+分钟跌多涨少"), "分时跌多涨少，先观望"),
        (re.compile(r"分时资金未见改善"), "分时承接一般"),
        (re.compile(r"距日内高点回撤[\d.]+%过大"), "冲高回落明显，不接"),
        (re.compile(r"距日内高点回撤[\d.]+%，现价[\d.]+有效低于分时均价[\d.]+"), "冲高回落且跌破均价"),
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


def _confirm_progress_suffix(audit_row: dict | None) -> str:
    """从审计行提取「5/10分，1/2轮」类进度后缀。"""
    if not audit_row:
        return ""
    status = str(audit_row.get("状态") or "")
    m = re.search(r"（([^）]+)）", status)
    if not m:
        return ""
    return f"（{m.group(1)}）"


def index_confirmation_audit(audit: list[dict] | None) -> dict[tuple[str, str], dict]:
    """按 (股票代码, 方向) 索引三确认审计行。"""
    out: dict[tuple[str, str], dict] = {}
    for row in audit or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("股票代码") or "").strip()
        action = str(row.get("方向") or "").strip()
        if code and action:
            out[(code, action)] = row
    return out


def pending_confirm_codes(audit: list[dict] | None) -> tuple[set[str], set[str]]:
    """仍在持续确认、尚未可执行的买卖代码。"""
    buy_codes: set[str] = set()
    sell_codes: set[str] = set()
    for row in audit or []:
        if not isinstance(row, dict) or row.get("可执行"):
            continue
        code = str(row.get("股票代码") or "").strip()
        action = str(row.get("方向") or "").strip()
        status = str(row.get("状态") or "")
        if not code or ("确认" not in status and "锁存" not in status):
            continue
        if action == "卖出":
            sell_codes.add(code)
        elif action == "买入":
            buy_codes.add(code)
    return buy_codes, sell_codes


def _audit_line(row: dict) -> str | None:
    if row.get("可执行"):
        return None
    name = str(row.get("股票名称") or row.get("股票代码") or "").strip()
    if not name:
        return None
    status = str(row.get("状态") or "")
    action = str(row.get("方向") or "")
    is_sell = action == "卖出"
    if is_sell and "等待14:30" in status:
        return f"{name}：卖点条件已满足，等尾盘最终确认"
    if is_sell and ("持续确认中" in status or "锁存" in status):
        return f"{name}：卖信号确认中"
    if is_sell and "确认" in status:
        return f"{name}：卖信号确认中"
    if not is_sell and "持续确认中" in status:
        return f"{name}：买点条件持续确认中"
    if not is_sell and "确认" in status:
        return f"{name}：出现买点信号，仍在持续确认"
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

    ms, _ = momentum_score(stock, mw_cfg)
    if ms < momentum_buy_floor(mw_cfg):
        return f"{name}：近端动能偏弱（{ms:.0f}分）"

    score = engine.score_stock(ctx, stock)
    if score.total < threshold:
        return f"{name}：强度还不够，再观察"

    ok, kind, setup_reason = detect_buy_setup(stock, ctx, mw_cfg)
    if not ok:
        note = _simplify_reason(setup_reason) or "买点未出"
        return f"{name}：{note}"

    if mode == "during_market":
        ok_intra, intra_note = intraday_allows_buy(stock, buy_cfg, buy_kind=kind)
        if not ok_intra:
            note = _simplify_reason(intra_note) or "分时偏弱，先不接"
            return f"{name}：{note}"

    return None


def _sample_lines(candidates: list[str], *, limit: int) -> list[str]:
    if len(candidates) <= limit:
        return candidates
    return random.sample(candidates, limit)


def _watchlist_skip_examples(
    ctx: ScoreContext,
    *,
    mode: str,
    limit: int = 2,
    exclude_codes: set[str] | None = None,
) -> list[str]:
    """仅从自选股（未持仓）抽样未满足买点的说明。"""
    mw_cfg = load_gates_config().get("main_wave") or {}
    buy_cfg = (load_gates_config().get("buy") or {}).get(
        "during_market" if mode == "during_market" else "pre_market"
    ) or {}
    engine = ScoringEngine()
    threshold = float(engine.config.get("buy_threshold", 72))
    held = {str(h.get("股票代码", "")).strip() for h in get_holdings()}
    skip = exclude_codes or set()

    candidates: list[str] = []
    for stock in ctx.payload.get("自选股") or []:
        if not isinstance(stock, dict):
            continue
        code = str(stock.get("股票代码", "")).strip()
        if not code or code in held or code in skip:
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


def _intraday_weakness_applies(ctx: ScoreContext, sell_cfg: dict) -> bool:
    weak = sell_cfg.get("intraday_weakness") or {}
    if not weak.get("enabled", True):
        return False
    modes = weak.get("modes") or ["during_market"]
    return bool(ctx.mode and ctx.mode in modes)


def _holding_hold_reason(
    holding: dict,
    enriched: dict,
    ctx: ScoreContext,
    *,
    mw_cfg: dict[str, Any],
    sell_cfg: dict[str, Any],
    engine: ScoringEngine,
) -> str:
    """持仓未出现在卖出信号里时的说明（始终返回一行）。"""
    from quant.scoring.tech_indicators import quote_last_price
    from quant.strategy.intraday import intraday_weakness_triggers_sell
    from quant.strategy.main_wave import detect_sell_setup
    from quant.strategy.time_stop import parse_buy_date, time_stop_triggers_sell

    code = str(holding.get("股票代码", "")).strip()
    name = str(holding.get("股票名称", "")).strip() or code
    if not code:
        return ""

    price = quote_last_price(enriched)
    if price is None:
        return f"{name}：暂无行情"
    try:
        buy_price = float(enriched.get("买入价", 0) or 0)
    except (TypeError, ValueError):
        buy_price = 0.0
    pnl_pct = (price - buy_price) / buy_price * 100 if buy_price > 0 else 0.0
    stop_loss = float(sell_cfg.get("stop_loss_pct", -5.0))
    score = engine.score_stock(ctx, enriched)
    sell_threshold = float(engine.config.get("sell_threshold", 45))

    if pnl_pct <= stop_loss:
        return f"{name}：触及止损线"
    ts_ok, _ = time_stop_triggers_sell(
        enriched, sell_cfg, pnl_pct=pnl_pct, buy_date=parse_buy_date(enriched)
    )
    if ts_ok:
        return f"{name}：时间止损待确认"
    if _intraday_weakness_applies(ctx, sell_cfg):
        ok_weak, _ = intraday_weakness_triggers_sell(enriched, sell_cfg)
        if ok_weak:
            return f"{name}：分时走弱，卖信号确认中"
    ok_sell, _, _ = detect_sell_setup(enriched, ctx, mw_cfg)
    if ok_sell:
        return f"{name}：卖点待确认"
    if pnl_pct >= 3.0:
        return f"{name}：浮盈{pnl_pct:.1f}%，持有"
    if pnl_pct <= stop_loss * 0.6:
        return f"{name}：浮亏{pnl_pct:.1f}%，未触发止损"
    if score.total >= sell_threshold + 8:
        return f"{name}：评分尚可，暂不减"
    return f"{name}：暂不减仓"


def _collect_holding_hold_reasons(
    ctx: ScoreContext,
    *,
    exclude_codes: set[str],
) -> list[str]:
    mw_cfg = load_gates_config().get("main_wave") or {}
    sell_cfg = load_gates_config().get("sell") or {}
    engine = ScoringEngine()
    out: list[str] = []
    for holding in resolve_payload_holdings(ctx.payload):
        if not isinstance(holding, dict):
            continue
        code = str(holding.get("股票代码", "")).strip()
        if not code or code in exclude_codes:
            continue
        enriched = holding
        for row in ctx.payload.get("持仓股") or []:
            if str(row.get("股票代码", "")).strip() == code:
                enriched = {**holding, **row}
                break
        line = _holding_hold_reason(
            holding, enriched, ctx, mw_cfg=mw_cfg, sell_cfg=sell_cfg, engine=engine
        )
        if line:
            out.append(line)
    return out


def _holding_skip_examples(ctx: ScoreContext, *, mode: str, limit: int = 2) -> list[str]:
    del mode
    return _sample_lines(
        _collect_holding_hold_reasons(ctx, exclude_codes=set()),
        limit=limit,
    )


def sample_no_trade_reasons(
    ctx: ScoreContext,
    *,
    mode: str,
    raw_buy: list[TradeSignal] | None = None,
    raw_sell: list[TradeSignal] | None = None,
    audit: list[dict] | None = None,
    extra_buy_exclude: set[str] | None = None,
    extra_sell_exclude: set[str] | None = None,
    per_side: int = 2,
) -> tuple[list[str], list[str]]:
    """无买卖信号时：自选未买、持仓未卖各随机抽样。"""
    buy_exclude = {s.code for s in raw_buy or []} | (extra_buy_exclude or set())
    sell_exclude = {s.code for s in raw_sell or []} | (extra_sell_exclude or set())
    pending_buy, pending_sell = pending_confirm_codes(audit)
    buy_exclude |= pending_buy
    sell_exclude |= pending_sell

    buy_pool = _watchlist_skip_examples(
        ctx,
        mode=mode,
        limit=max(per_side * 3, 6),
        exclude_codes=buy_exclude,
    )
    sell_pool = _collect_holding_hold_reasons(ctx, exclude_codes=sell_exclude)

    buy_lines = _sample_lines(buy_pool, limit=per_side)
    sell_lines = _sample_lines(sell_pool, limit=per_side)

    if not sell_lines:
        held = resolve_payload_holdings(ctx.payload)
        fallback = [
            f"{str(h.get('股票名称') or h.get('股票代码', '')).strip()}：暂不减仓"
            for h in held
            if isinstance(h, dict)
            and str(h.get("股票代码", "")).strip()
            and str(h.get("股票代码", "")).strip() not in sell_exclude
        ]
        sell_lines = _sample_lines(fallback, limit=per_side)

    return buy_lines, sell_lines


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
