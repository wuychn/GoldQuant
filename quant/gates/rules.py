"""硬门禁规则（与评分分离，不可被 ML 自动修改）。

检查顺序（买入）
----------------
1. 标的池（板块、ST）
2. 止损冷却期
3. 全局门禁（熔断、每日亏损）

全局门禁不通过时不开新仓；卖出信号仍可由 signals/sell 独立触发。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant.config import load_gates_config
from quant.constants import STRATEGY_NAME
from quant.narrative.push_style import profit_effect_level
from quant.scoring.context import ScoreContext, index_change, infer_regime
from quant.store.state import (
    codes_sold_today,
    compute_holdings_market_value,
    get_cash,
    get_holdings,
    get_total_assets,
    stoploss_cooldown_codes,
    sum_today_realized_pnl,
)
from app.utils.common_util import is_allowed_symbol_pool_code, normalize_a_share_code


@dataclass
class GateResult:
    passed: bool
    name: str
    reason: str = ""


@dataclass
class GateReport:
    results: list[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def summary(self) -> str:
        """内部日志/调试用语。"""
        fails = [r for r in self.results if not r.passed]
        if not fails:
            return "全局门禁通过"
        return "；".join(f"{r.name}:{r.reason}" for r in fails)

    def push_summary(self, payload: dict | None = None) -> str:
        """飞书推送/LLM 正文用的自然表述。"""
        fails = [r for r in self.results if not r.passed]
        if not fails:
            level = profit_effect_level(payload)
            return f"赚钱效应{level}，{format_position_control(payload)}"
        labels = {
            "极端熔断": "大盘急跌",
            "每日亏损限额": "当日亏损偏大",
            "标的池": "标的不在范围",
            "止损冷却": "止损后冷却",
            "当日卖出冷却": "当日已卖出",
            "全局门禁": "赚钱效应",
        }
        parts: list[str] = []
        for r in fails:
            label = labels.get(r.name, r.name)
            if r.reason:
                parts.append(f"{label}（{r.reason}）")
            else:
                parts.append(label)
        return "；".join(parts) + "，暂不新开仓"


def _symbol_ok(code: str, name: str, cfg: dict) -> GateResult:
    pool = cfg.get("symbol_pool") or {}
    prefixes = pool.get("prefixes") or ["60", "00", "30"]
    norm = normalize_a_share_code(code)
    if not is_allowed_symbol_pool_code(norm, prefixes=tuple(prefixes)):
        return GateResult(False, "标的池", f"{code} 不在允许板块")
    if pool.get("exclude_st") and ("ST" in name.upper() or name.startswith("*")):
        return GateResult(False, "标的池", f"{name} 为 ST")
    return GateResult(True, "标的池")


def check_global_gates(ctx: ScoreContext) -> GateReport:
    """全局前置：极端熔断、每日亏损限额。"""
    cfg = load_gates_config()
    results: list[GateResult] = []

    cb = cfg.get("circuit_breaker") or {}
    idx_drop = float(cb.get("index_drop_pct", -2.0))
    chg = index_change(ctx.payload)
    if chg is not None and chg <= idx_drop:
        results.append(
            GateResult(False, "极端熔断", f"上证{chg:.2f}%，跌幅超过{abs(idx_drop):.1f}%")
        )

    limit = float(cfg.get("daily_loss_limit_pct", -3.0))
    total = get_total_assets()
    pnl = sum_today_realized_pnl()
    if total > 0 and pnl / total * 100 <= limit:
        pct = pnl / total * 100
        results.append(
            GateResult(False, "每日亏损限额", f"当日亏损约{pct:.2f}%，超过{abs(limit):.1f}%上限")
        )

    if not results:
        results.append(GateResult(True, "全局门禁"))
    return GateReport(results=results)


def check_buy_gates(stock: dict, ctx: ScoreContext) -> GateReport:
    """单票买入前：标的池 + 冷却 + 全局。"""
    cfg = load_gates_config()
    code = str(stock.get("股票代码", "")).strip()
    name = str(stock.get("股票名称", "")).strip()
    results = [_symbol_ok(code, name, cfg)]

    cooldown = int(cfg.get("stoploss_cooldown_days", 3))
    if code in stoploss_cooldown_codes(cooldown):
        results.append(GateResult(False, "止损冷却", f"{code} 在冷却期"))

    if cfg.get("block_same_day_rebuy_after_sell", True) and code in codes_sold_today():
        results.append(GateResult(False, "当日卖出冷却", f"{code} 当日已卖出，不再开仓"))

    global_report = check_global_gates(ctx)
    results.extend(global_report.results)
    return GateReport(results=results)


def format_position_control(payload: dict | None) -> str:
    """推送用仓位上限表述（不含赚钱效应档位）。"""
    limits = position_limits(ScoreContext.from_payload(payload)) if payload else None
    held = active_holding_count()
    if limits:
        per = per_stock_pct_at_full(limits, STRATEGY_NAME)
        per_s = f"{per:.1f}".rstrip("0").rstrip(".")
        return (
            f"总仓位上限{limits['total_pct']:.0f}%，"
            f"最多持仓{limits['max_stocks']}只，"
            f"满配单票约{per_s}%，"
            f"当前持仓{held}只"
        )
    return f"总仓位上限50%，最多持仓3只，满配单票约16.7%，当前持仓{held}只"


def position_limits(ctx: ScoreContext) -> dict:
    """按 infer_regime(payload) 返回 quant.yml gates.position 块。"""
    cfg = load_gates_config()
    regime = infer_regime(ctx.payload)
    block = (cfg.get("position") or {}).get(regime) or (cfg.get("position") or {}).get("震荡") or {}
    return {
        "regime": regime,
        "total_pct": float(block.get("total_pct", 50)),
        "max_stocks": int(block.get("max_stocks", 3)),
        "single_pct": block.get("single_pct") or {},
    }


def per_stock_pct_at_full(limits: dict, strategy: str) -> float:
    """满配时单票目标占比：默认 total_pct / max_stocks，可被 single_pct 覆盖。"""
    overrides = limits.get("single_pct") or {}
    if strategy in overrides:
        return float(overrides[strategy])
    max_stocks = max(1, int(limits.get("max_stocks", 1)))
    return float(limits.get("total_pct", 50)) / max_stocks


def active_holding_count(holdings: list[dict] | None = None) -> int:
    rows = holdings if holdings is not None else get_holdings()
    return sum(1 for h in rows if int(h.get("持仓股数", 0) or 0) > 0)


def calc_buy_quantity(stock: dict, ctx: ScoreContext, price: float) -> int:
    """按总仓位上限与剩余空位均分预算，计算买入股数（100 股整数倍）。

    例：震荡 total=50%、max=3 → 空仓时每笔约 16.7% 总资产，满 3 只合计约 50%。
    """
    qty_map = allocate_buy_quantities_by_score(
        [(1.0, {**stock, "战法": str(stock.get("战法", STRATEGY_NAME))}, price)],
        ctx,
    )
    code = str(stock.get("股票代码", "")).strip()
    return qty_map.get(code, 0)


def allocate_buy_quantities_by_score(
    candidates: list[tuple[float, dict, float]],
    ctx: ScoreContext,
) -> dict[str, int]:
    """多候选按评分比例分配剩余仓位预算（100 股整数倍）。"""
    if not candidates:
        return {}

    limits = position_limits(ctx)
    max_stocks = int(limits["max_stocks"])
    held = active_holding_count()
    if held >= max_stocks:
        return {}

    total_assets = get_total_assets()
    if total_assets <= 0:
        return {}

    current_mv = compute_holdings_market_value(get_holdings())
    total_cap = total_assets * float(limits["total_pct"]) / 100
    room = max(0.0, total_cap - current_mv)
    if room <= 0:
        return {}

    scores = [max(float(s), 1.0) for s, _, _ in candidates]
    score_sum = sum(scores)
    if score_sum <= 0:
        return {}

    remaining_cash = get_cash()
    result: dict[str, int] = {}

    for (score, stock, price), w in zip(candidates, scores):
        del score
        if price <= 0:
            continue
        code = str(stock.get("股票代码", "")).strip()
        if not code:
            continue
        strategy = str(stock.get("战法", STRATEGY_NAME))
        budget = room * (w / score_sum)
        cap_value = total_assets * per_stock_pct_at_full(limits, strategy) / 100
        budget = min(budget, cap_value, remaining_cash)
        qty = int(budget / price / 100) * 100
        if qty >= 100:
            result[code] = qty
            remaining_cash -= qty * price

    return result
