"""硬门禁规则（与评分分离，不可被 ML 自动修改）。

检查顺序（买入）
----------------
1. 标的池（板块、ST）
2. 止损冷却期
3. 全局门禁（熔断、每日亏损、连续缩量）

全局门禁不通过时不开新仓；卖出信号仍可由 signals/sell 独立触发。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant.config import load_gates_config
from quant.market.turnover import load_completed_day_turnovers
from quant.scoring.context import ScoreContext, index_change, infer_regime
from quant.store.state import get_total_assets, stoploss_cooldown_codes, sum_today_realized_pnl
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
        fails = [r for r in self.results if not r.passed]
        if not fails:
            return "全局门禁通过"
        return "；".join(f"{r.name}:{r.reason}" for r in fails)


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
    """全局前置：极端熔断、每日亏损限额、连续缩量。"""
    cfg = load_gates_config()
    results: list[GateResult] = []

    cb = cfg.get("circuit_breaker") or {}
    idx_drop = float(cb.get("index_drop_pct", -2.0))
    chg = index_change(ctx.payload)
    if chg is not None and chg <= idx_drop:
        results.append(GateResult(False, "极端熔断", f"上证涨跌幅{chg:.2f}%≤{idx_drop}%"))

    limit = float(cfg.get("daily_loss_limit_pct", -3.0))
    total = get_total_assets()
    pnl = sum_today_realized_pnl()
    if total > 0 and pnl / total * 100 <= limit:
        results.append(GateResult(False, "每日亏损限额", f"当日已实现盈亏达{limit}%"))

    shrink_days = int(cb.get("shrink_volume_days", 3))
    shrink_ratio = float(cb.get("shrink_volume_ratio", 0.8))
    # 连续缩量：比较最近 N 个已收盘日的全市场成交额（evening 归档），
    # 不用「上证-收盘价」（那是指数点位）也不拿盘中累计与全天混比。
    amounts = load_completed_day_turnovers(count=shrink_days)
    if len(amounts) == shrink_days and amounts[0] > 0:
        if (
            all(amounts[i] > amounts[i + 1] for i in range(len(amounts) - 1))
            and amounts[-1] < amounts[0] * shrink_ratio
        ):
            results.append(
                GateResult(False, "连续缩量", f"近{shrink_days}日全市场成交额连续缩量")
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

    global_report = check_global_gates(ctx)
    results.extend(global_report.results)
    return GateReport(results=results)


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


def calc_buy_quantity(stock: dict, ctx: ScoreContext, price: float) -> int:
    """按战法单票上限与可用资金计算买入股数（100 股整数倍）。"""
    limits = position_limits(ctx)
    total_assets = get_total_assets()
    strategy = str(stock.get("战法", STRATEGY_NAME))
    single_pct = float((limits["single_pct"] or {}).get(strategy, 8))
    budget = total_assets * single_pct / 100
    if price <= 0:
        return 0
    qty = int(budget / price / 100) * 100
    return max(qty, 0)
