"""按日 alpha 构建（统一 IC 权重口径）。"""

from __future__ import annotations

from quant.config import load_factor_weights
from quant.factors.compose import compose_alpha
from quant.factors.panel_builder import build_panel
from quant.factors.registry import REGISTRY


def build_alpha_by_date(
    dates: list[str],
    daily,
    *,
    use_ic_weights: bool = True,
    workers: int = 1,
) -> dict[str, dict[str, float]]:
    panel = build_panel(dates, daily=daily, workers=workers)
    by_date: dict[str, dict[str, float]] = {}
    rows_by: dict[str, list] = {}
    for r in panel:
        rows_by.setdefault(r.date, []).append(r)
    default_w = REGISTRY.weights()
    for d in sorted(rows_by):
        rows = rows_by[d]
        w = load_factor_weights(as_of=d) if use_ic_weights else None
        # strict OOS 且无 factor_weights_ts 时回退 REGISTRY 默认（非 IC 权重）
        by_date[d] = compose_alpha(rows, weights=w or default_w)
    return by_date
