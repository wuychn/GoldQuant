"""IC 驱动的因子权重：用历史 ICIR 决定因子权重，替代手工默认权重。

权重 ∝ 截面 IC 的信息比（ICIR = ic_mean / ic_std），仅保留 IC 显著为正的因子；
负 IC 或噪声因子权重置 0（在 ``compose_alpha`` 中被 ``w<=0`` 跳过）。
direction 已在面板预乘（``panel_builder``），故权重保持非负，不二次翻转。

``compose_alpha`` 的合成是 z-score 的**加权均值**（num/den），对权重绝对尺度不敏感，
只看相对比例 → ICIR 比例权重可直接使用。
"""

from __future__ import annotations


def icir_weights(
    ic_report: dict,
    *,
    min_icir: float = 0.0,
    min_ic_mean: float = 0.0,
    min_tstat: float = 1.0,
) -> dict[str, float]:
    """从 IC 报告产出 {factor: weight}（仅含通过阈值的因子）。

    - ``min_icir`` / ``min_ic_mean`` / ``min_tstat``：三项须同时满足才入选，
      滤掉 IC 噪声与负向因子。
    """
    out: dict[str, float] = {}
    for name, m in (ic_report or {}).items():
        if not isinstance(m, dict):
            continue
        icir = m.get("icir")
        ic_mean = m.get("ic_mean")
        tstat = m.get("t_stat", 0.0)
        if icir is None or ic_mean is None:
            continue
        try:
            icir = float(icir)
            ic_mean = float(ic_mean)
            tstat = float(tstat or 0.0)
        except (TypeError, ValueError):
            continue
        if ic_mean < min_ic_mean:
            continue
        if icir < min_icir:
            continue
        if tstat < min_tstat:
            continue
        out[name] = icir if icir > 0 else 0.0
    return out


def full_weight_map(
    ic_report: dict,
    all_factors: list[str],
    *,
    min_icir: float = 0.0,
    min_ic_mean: float = 0.0,
    min_tstat: float = 1.0,
    floor: float = 0.0,
) -> dict[str, float]:
    """完整权重表：通过阈值的因子取 ICIR 权重，其余 0（供 compose_alpha 全覆盖）。"""
    positive = icir_weights(
        ic_report, min_icir=min_icir, min_ic_mean=min_ic_mean, min_tstat=min_tstat
    )
    out = {name: floor for name in all_factors}
    for name, w in positive.items():
        if name in out:
            out[name] = max(floor, w)
    return out
