"""个股结构分（替代 R1 百分制主阀门）。"""

from __future__ import annotations

from quant.config import load_gates_config
from quant.scoring.context import ScoreContext
from quant.strategy.main_wave import detect_buy_setup, main_wave_phase


def structure_score(stock: dict) -> float:
    """0～100：主升浪结构 + 买点可读性。"""
    cfg = load_gates_config().get("main_wave") or {}
    ok, phase, _ = main_wave_phase(stock, cfg)
    if not ok:
        return 20.0
    ctx = ScoreContext.from_payload({}, mode="")
    buy_ok, _, _ = detect_buy_setup(stock, ctx, cfg)
    base = {"加速": 85.0, "回调": 78.0, "趋势": 70.0}.get(phase, 55.0)
    if buy_ok:
        base += 5.0
    return min(100.0, base)
