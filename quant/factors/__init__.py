"""截面因子层：library 因子 → 行业/市值中性化 → 合成 alpha（r3 面板）。

接口：quant.factors.compose / panel_builder / ic / registry / library / weights。
"""

from __future__ import annotations

from quant.factors.neutralize import neutralize_panel_rows

__all__ = ["neutralize_panel_rows"]
