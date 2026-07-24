"""Regime v2：连续分值 + 按交易日滞后，映射仓位。"""

from __future__ import annotations

from quant.config import load_gates_config, load_quant_config
from quant.scoring.context import index_change, infer_regime, profit_effect, zt_height, zt_pool
from quant.timeutil import cn_date_str


def _raw_regime_score(payload: dict) -> float:
    """0=弱势, 50=震荡, 100=强势 的连续分值。"""
    profit = profit_effect(payload)
    up = int(profit.get("上涨", 0) or 0)
    down = int(profit.get("下跌", 0) or 0)
    zt_cnt = int(profit.get("涨停", len(zt_pool(payload))) or len(zt_pool(payload)))
    idx_chg = index_change(payload) or 0.0
    height = zt_height(payload)

    score = 50.0
    score += max(-15, min(15, idx_chg * 5))
    if up > down:
        score += 10
    elif up < down:
        score -= 10
    score += max(-10, min(10, (zt_cnt - 45) / 5))
    score += max(-8, min(8, (height - 3) * 4))
    return max(0.0, min(100.0, score))


class RegimeTracker:
    """带 hysteresis 的 regime 跟踪（按交易日计数，同日多次 refresh 不加速切换）。"""

    def __init__(self, *, hysteresis_days: int = 2):
        self.hysteresis_days = hysteresis_days
        self._last_label = "震荡"
        self._pending_label = "震荡"
        self._pending_days = 0
        self._last_date = ""
        self._last_raw = 50.0

    @property
    def label(self) -> str:
        return self._last_label

    @property
    def last_raw(self) -> float:
        return self._last_raw

    @property
    def last_date(self) -> str:
        return self._last_date

    def update(self, payload: dict, *, date_str: str | None = None) -> str:
        raw = _raw_regime_score(payload)
        self._last_raw = raw
        if raw >= 70:
            candidate = "强势"
        elif raw <= 30:
            candidate = "弱势"
        else:
            candidate = "震荡"

        day = date_str or cn_date_str()
        # 同日多次调用：只刷新候选标签，不累计 pending_days
        if day == self._last_date:
            if candidate == self._last_label:
                self._pending_label = candidate
                self._pending_days = 0
            elif candidate != self._pending_label:
                self._pending_label = candidate
            return self._last_label

        self._last_date = day

        if candidate == self._last_label:
            self._pending_label = candidate
            self._pending_days = 0
            return self._last_label

        if candidate == self._pending_label:
            self._pending_days += 1
        else:
            self._pending_label = candidate
            self._pending_days = 1

        if self._pending_days >= self.hysteresis_days:
            self._last_label = candidate
        return self._last_label


_TRACKER: RegimeTracker | None = None


def get_regime_tracker() -> RegimeTracker:
    """进程级单例；回测请用 reset_regime_tracker 隔离。"""
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = RegimeTracker()
    return _TRACKER


def reset_regime_tracker(tracker: RegimeTracker | None = None) -> RegimeTracker:
    """重置 / 注入 tracker（回测隔离）。"""
    global _TRACKER
    _TRACKER = tracker if tracker is not None else RegimeTracker()
    return _TRACKER


def refresh_regime(payload: dict, *, date_str: str | None = None) -> str:
    """每个盘中快照调用一次，更新 hysteresis 状态。"""
    return get_regime_tracker().update(payload, date_str=date_str)


def infer_regime_v2(payload: dict, tracker: RegimeTracker | None = None) -> str:
    """连续分值 + 可选滞后。

    传入 tracker 时只读 label（不 update），避免同日多次调用加速切换。
    无 tracker 时用瞬时 raw 分档；生产路径应先 refresh_regime 再读 get_regime_tracker()。
    """
    cfg = load_quant_config().get("research") or {}
    if not cfg.get("regime_v2_enabled", True):
        return infer_regime(payload)
    if tracker is not None:
        return tracker.label
    raw = _raw_regime_score(payload)
    if raw >= 70:
        return "强势"
    if raw <= 30:
        return "弱势"
    return "震荡"


def position_pct_from_regime(regime: str) -> dict:
    """返回 gates.position 块。"""
    cfg = load_gates_config()
    block = (cfg.get("position") or {}).get(regime) or (cfg.get("position") or {}).get("震荡") or {}
    return {
        "regime": regime,
        "total_pct": float(block.get("total_pct", 50)),
        "max_stocks": int(block.get("max_stocks", 3)),
        "single_pct": block.get("single_pct") or {},
    }


def continuous_total_pct(
    regime_score: float,
    *,
    min_pct: float = 20.0,
    max_pct: float = 80.0,
) -> float:
    """连续映射总仓位：默认 20% ~ 80%。"""
    lo = float(min_pct)
    hi = float(max_pct)
    span = max(0.0, hi - lo)
    return lo + max(0.0, min(100.0, regime_score)) / 100.0 * span
