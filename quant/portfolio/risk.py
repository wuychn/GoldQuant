"""组合级风控：最大回撤止损、日亏升级。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from quant.config import load_quant_config
from quant.store.paths import ensure_layout, state_file
from quant.timeutil import cn_date_str


@dataclass
class PortfolioRiskState:
    peak_equity: float = 0.0
    drawdown_halt: bool = False
    halt_until_date: str = ""


def _risk_cfg() -> dict:
    return (load_quant_config().get("portfolio") or {}).get("risk") or {}


def update_peak(equity: float, state: PortfolioRiskState) -> None:
    state.peak_equity = max(state.peak_equity, equity)


def check_drawdown_halt(
    equity: float,
    date_str: str,
    state: PortfolioRiskState,
) -> tuple[bool, str]:
    """滚动 peak-to-trough 回撤超阈值 → 禁止新开仓。"""
    cfg = _risk_cfg()
    if not cfg.get("max_drawdown_halt_enabled", True):
        return True, ""
    if state.halt_until_date and date_str <= state.halt_until_date:
        return False, "组合回撤止损冷却中"

    max_dd = float(cfg.get("max_drawdown_pct", 15)) / 100
    if state.peak_equity > 0:
        dd = (state.peak_equity - equity) / state.peak_equity
        if dd >= max_dd:
            state.drawdown_halt = True
            days = int(cfg.get("halt_days", 5))
            from datetime import datetime, timedelta

            try:
                d = datetime.strptime(date_str, "%Y-%m-%d").date()
                state.halt_until_date = (d + timedelta(days=days)).isoformat()
            except ValueError:
                state.halt_until_date = date_str
            return False, f"组合回撤{dd*100:.1f}%超阈值"
    return True, ""


def daily_loss_force_reduce(
    daily_pnl_pct: float,
) -> bool:
    """日亏超阈值 → 强制减仓（由信号层消费）。"""
    cfg = _risk_cfg()
    threshold = float(cfg.get("force_reduce_daily_loss_pct", -5.0))
    return daily_pnl_pct <= threshold


def load_risk_state() -> PortfolioRiskState:
    ensure_layout()
    path = state_file("portfolio_risk.json")
    if not path.is_file():
        return PortfolioRiskState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PortfolioRiskState()
    if not isinstance(data, dict):
        return PortfolioRiskState()
    return PortfolioRiskState(
        peak_equity=float(data.get("peak_equity") or 0),
        drawdown_halt=bool(data.get("drawdown_halt")),
        halt_until_date=str(data.get("halt_until_date") or ""),
    )


def save_risk_state(state: PortfolioRiskState) -> None:
    ensure_layout()
    path = state_file("portfolio_risk.json")
    path.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def evaluate_live_drawdown_halt(
    *,
    equity: float | None = None,
    date_str: str | None = None,
) -> tuple[bool, str]:
    """实盘路径：用当前总资产更新 peak 并检查回撤熔断，持久化状态。"""
    from quant.store.state import get_total_assets

    eq = float(equity if equity is not None else get_total_assets())
    d = date_str or cn_date_str()
    state = load_risk_state()
    if state.peak_equity <= 0 and eq > 0:
        state.peak_equity = eq
    update_peak(eq, state)
    ok, reason = check_drawdown_halt(eq, d, state)
    # 冷却期满后清除 halt 标记
    if ok and state.halt_until_date and d > state.halt_until_date:
        state.drawdown_halt = False
        state.halt_until_date = ""
    save_risk_state(state)
    return ok, reason
