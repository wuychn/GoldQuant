"""截面因子层：原始因子 → 行业/市值中性化 → 合成 alpha。

旧 API（compose_neutral_alpha / FactorPanel / compute_raw_factors 等）通过
模块 __getattr__ 惰性加载，避免 import quant.factors.* 时拉起 yaml/akshare 重链。
新 r3 接口在 quant.factors.compose / panel_builder / ic / registry。
"""

from __future__ import annotations

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

# neutralize_panel_rows 是纯 numpy，可即时导出（新代码常用）
from quant.factors.neutralize import neutralize_panel_rows  # noqa: E402


def __getattr__(name: str):
    if name == "compose_neutral_alpha" or name == "map_alpha_to_score":
        from quant.factors.compose import compose_neutral_alpha, map_alpha_to_score

        return {"compose_neutral_alpha": compose_neutral_alpha, "map_alpha_to_score": map_alpha_to_score}[name]
    if name == "FactorPanel" or name == "build_factor_panel":
        from quant.factors.panel import FactorPanel, build_factor_panel

        return {"FactorPanel": FactorPanel, "build_factor_panel": build_factor_panel}[name]
    if name == "compute_raw_factors":
        from quant.factors.raw import compute_raw_factors

        return compute_raw_factors
    if name == "compare_raw_vs_neutral_ic" or name == "run_factor_research":
        from quant.factors.report import compare_raw_vs_neutral_ic, run_factor_research

        return {"compare_raw_vs_neutral_ic": compare_raw_vs_neutral_ic, "run_factor_research": run_factor_research}[name]
    raise AttributeError(f"module 'quant.factors' has no attribute {name!r}")
