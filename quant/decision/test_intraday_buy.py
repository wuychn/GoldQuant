"""execute_intraday_buys 目标权重约束测试（批次3·执行接线）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from quant.factors.library.intraday import SpotRow
from quant.store.paths import override_quant_home


def _sr(code: str, last: float) -> SpotRow:
    return SpotRow(
        code=code, last=last, open=last * 0.98, pre_close=last * 0.97,
        pct=3.0, volume=1e6, amount=1e7, vol_ratio=2.0, turnover=1.0, speed=0.5,
    )


def test_intraday_buy_respects_target_weights():
    """盘中买入受 TargetPortfolio 目标权重约束：不在目标池的票被拒，仓位 ≤ target_w。"""
    from quant.decision.paper_execute import execute_intraday_buys

    buys = [_sr("600000", 10.0), _sr("600001", 11.0)]
    alpha_z = {"600000": 1.5, "600001": 1.2}
    target_weights = {"600000": 0.06}  # 600001 不在目标池
    with tempfile.TemporaryDirectory(prefix="gq-intraday-") as td:
        with override_quant_home(Path(td)):
            result = execute_intraday_buys(
                buys, alpha_z, name_map={"600000": "A", "600001": "B"},
                today="2024-06-28", target_weights=target_weights, dry_run=True,
            )
    # 600001 不在目标池 → 拒（修复 P0：盘中买入受 TargetPortfolio 约束，不再扁平分档）
    assert result["rejected"].get("600001") == "not_in_target"
    # 600000 在目标池（target_w=0.06 < 分档 0.0875）→ 进入 signals
    assert result["n_signals"] >= 1


def test_intraday_buy_no_target_weights_fallback():
    """无 target_weights（旧 battle_pool）→ 回退原分档，向后兼容。"""
    from quant.decision.paper_execute import execute_intraday_buys

    buys = [_sr("600000", 10.0)]
    alpha_z = {"600000": 1.5}
    with tempfile.TemporaryDirectory(prefix="gq-intraday2-") as td:
        with override_quant_home(Path(td)):
            result = execute_intraday_buys(
                buys, alpha_z, name_map={"600000": "A"},
                today="2024-06-28", target_weights=None, dry_run=True,
            )
    assert result["n_signals"] >= 1
    assert "600000" not in (result.get("rejected") or {})
