"""截面因子层：原始因子 → 行业/市值中性化 → 合成 alpha。"""

from __future__ import annotations

from quant.factors.compose import compose_neutral_alpha, map_alpha_to_score
from quant.factors.neutralize import neutralize_panel_rows
from quant.factors.panel import FactorPanel, build_factor_panel
from quant.factors.raw import compute_raw_factors
from quant.factors.report import compare_raw_vs_neutral_ic, run_factor_research

__all__ = [
    "FactorPanel",
    "build_factor_panel",
    "compute_raw_factors",
    "neutralize_panel_rows",
    "compose_neutral_alpha",
    "map_alpha_to_score",
    "compare_raw_vs_neutral_ic",
    "run_factor_research",
]
