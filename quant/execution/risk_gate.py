"""风控门禁（Phase 3）：把此前"死配置"真正生效。

接入实盘 executor 的买入前置检查：
- ``daily_loss_limit_pct``：按**总权益**（含浮亏）日内回撤计，非仅已实现盈亏。
- ``circuit_breaker.index_drop_pct``：大盘跌幅破阈值 → 禁开仓。
- ``portfolio.risk.max_drawdown_halt``：组合回撤超阈值 → 停 N 日。
- ``stoploss_cooldown_days``：止损卖出后 N 日内不回补同代码（``stoploss_cooldown_codes`` 已存在，此处接入）。
- ``block_same_day_rebuy_after_sell``：当日卖出代码当日不回补（``codes_sold_today`` 已存在）。

核心判定 ``assess_buy_gate`` 为纯函数（无副作用、可单测）；状态读写由薄包装承担。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from quant.store.paths import state_file
from quant.timeutil import cn_today


@dataclass
class RiskDecision:
    allow_new_buy: bool = True
    blocked_codes: set[str] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)


def assess_buy_gate(
    *,
    total_assets: float,
    day_start_equity: float,
    index_change_pct: float | None,
    drawdown_from_peak_pct: float | None,
    cooldown_codes: set[str],
    sold_today_codes: set[str],
    daily_loss_limit_pct: float,
    circuit_breaker_pct: float | None,
    max_drawdown_pct: float | None,
    halt_active: bool,
    block_same_day_rebuy: bool,
) -> RiskDecision:
    """纯函数：综合各项风控判定是否允许新开仓 + 逐代码黑名单。"""
    dec = RiskDecision()
    # 逐代码黑名单：止损冷却 + 当日已卖
    if cooldown_codes:
        dec.blocked_codes |= {c for c in cooldown_codes if c}
    if block_same_day_rebuy and sold_today_codes:
        dec.blocked_codes |= {c for c in sold_today_codes if c}

    # 全局禁开仓条件
    if halt_active:
        dec.allow_new_buy = False
        dec.reasons.append("回撤熔断停牌中")

    if day_start_equity and day_start_equity > 0 and total_assets >= 0:
        daily_loss_pct = (total_assets / day_start_equity - 1.0) * 100.0
        if daily_loss_pct <= daily_loss_limit_pct:
            dec.allow_new_buy = False
            dec.reasons.append(f"日内亏损{daily_loss_pct:.2f}%≤{daily_loss_limit_pct}%")

    if circuit_breaker_pct is not None and index_change_pct is not None:
        if index_change_pct <= circuit_breaker_pct:
            dec.allow_new_buy = False
            dec.reasons.append(f"大盘{index_change_pct:.2f}%≤{circuit_breaker_pct}%")

    if max_drawdown_pct is not None and drawdown_from_peak_pct is not None:
        if abs(drawdown_from_peak_pct) >= max_drawdown_pct:
            dec.allow_new_buy = False
            dec.reasons.append(f"组合回撤{abs(drawdown_from_peak_pct):.2f}%≥{max_drawdown_pct}%")
    return dec


# ---------------- 状态读写（薄包装） ----------------


def _halt_path():
    return state_file("risk_halt.json")


def is_halt_active() -> bool:
    """组合回撤熔断是否仍在停牌期。"""
    p = _halt_path()
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    halt_until = str(data.get("halt_until", ""))[:10]
    if not halt_until:
        return False
    try:
        return cn_today().isoformat() <= date.fromisoformat(halt_until).isoformat()
    except ValueError:
        return False


def trigger_halt(*, halt_days: int, drawdown_pct: float) -> None:
    """触发回撤熔断：写入停牌截止日。"""
    if halt_days <= 0:
        return
    until = cn_today().toordinal() + halt_days
    data = {
        "halt_until": date.fromordinal(until).isoformat(),
        "triggered_at": cn_today().isoformat(),
        "drawdown_pct": round(float(drawdown_pct), 2),
    }
    _halt_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def clear_halt() -> None:
    p = _halt_path()
    if p.is_file():
        p.unlink()


def drawdown_from_peak_pct() -> float | None:
    """从权益曲线峰值到当前权益的回撤（负百分点）。无曲线返回 None。"""
    path = state_file("equity.jsonl")
    if not path.is_file():
        return None
    peak = None
    last = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            v = row.get("总资产")
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v <= 0:
                continue
            peak = v if peak is None else max(peak, v)
            last = v
    except (json.JSONDecodeError, OSError):
        return None
    if peak is None or last is None or peak <= 0:
        return None
    return (last / peak - 1.0) * 100.0


def get_or_init_day_start_equity(total_assets: float) -> float:
    """日内基准权益：当日首次调用时落当前总资产，跨日重置。"""
    import json as _json

    path = state_file("day_start_equity.json")
    today = cn_today().isoformat()
    if path.is_file():
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("date", "")) == today:
                return float(data.get("equity", 0) or 0)
        except (ValueError, OSError):
            pass
    equity = float(total_assets) if total_assets and total_assets > 0 else 0.0
    path.write_text(_json.dumps({"date": today, "equity": equity}, ensure_ascii=False), encoding="utf-8")
    return equity


# ---------------- 配置读取 ----------------


def _risk_config() -> tuple[dict, dict, dict]:
    from quant.config import load_gates_config, load_quant_config

    gates = load_gates_config() or {}
    trading = gates.get("trading") or {}
    cb = gates.get("circuit_breaker") or {}
    portfolio = (load_quant_config() or {}).get("portfolio") or {}
    risk = portfolio.get("risk") or {}
    return trading, risk, cb  # type: ignore[return-value]


def fetch_index_change_pct() -> float | None:
    """大盘涨跌幅（百分点）。取数失败返回 None（不阻断）。"""
    try:
        from quant.data.fetch import fetch_spot_em

        df = fetch_spot_em()
        if df is None or df.empty or "pct" not in df.columns:
            return None
        sh = df[df["code"].astype(str).str.startswith("000001")]
        if sh.empty:
            return None
        v = float(sh.iloc[0]["pct"])
        return v if v == v else None
    except Exception:
        return None


def build_risk_context(*, total_assets: float, cooldown_days: int | None = None) -> RiskDecision:
    """从状态 + 配置汇聚风控上下文，返回买入判定。executor 在买入阶段前调用。"""
    from quant.store.state import codes_sold_today, stoploss_cooldown_codes

    trading, risk, cb = _risk_config()
    daily_loss_limit = float(trading.get("daily_loss_limit_pct", -3.0))
    block_rebuy = bool(trading.get("block_same_day_rebuy_after_sell", True))
    circuit_pct = cb.get("index_drop_pct")
    circuit_pct = float(circuit_pct) if circuit_pct is not None else None

    dd_pct = drawdown_from_peak_pct()
    halt_enabled = bool(risk.get("max_drawdown_halt_enabled", True))
    max_dd = risk.get("max_drawdown_pct")
    max_dd = float(max_dd) if max_dd is not None else None
    halt_days = int(risk.get("halt_days", 5))

    halt_active = is_halt_active()
    # 触发熔断（幂等：已在停牌期则不重复触发）
    if (
        not halt_active
        and halt_enabled
        and max_dd is not None
        and dd_pct is not None
        and abs(dd_pct) >= max_dd
    ):
        trigger_halt(halt_days=halt_days, drawdown_pct=dd_pct)
        halt_active = True

    days = cooldown_days if cooldown_days is not None else int(trading.get("stoploss_cooldown_days", 3))
    cooldown = stoploss_cooldown_codes(days)
    sold = codes_sold_today()
    day_start = get_or_init_day_start_equity(total_assets)
    idx_chg = fetch_index_change_pct()

    return assess_buy_gate(
        total_assets=total_assets,
        day_start_equity=day_start,
        index_change_pct=idx_chg,
        drawdown_from_peak_pct=dd_pct,
        cooldown_codes=cooldown,
        sold_today_codes=sold,
        daily_loss_limit_pct=daily_loss_limit,
        circuit_breaker_pct=circuit_pct,
        max_drawdown_pct=max_dd,
        halt_active=halt_active,
        block_same_day_rebuy=block_rebuy,
    )
