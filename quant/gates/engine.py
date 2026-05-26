"""门禁引擎入口。"""

from __future__ import annotations

from quant.gates.rules import GateReport, check_buy_gates, check_global_gates, position_limits
from quant.scoring.context import ScoreContext

__all__ = [
    "GateReport",
    "check_buy_gates",
    "check_global_gates",
    "position_limits",
    "ScoreContext",
]
