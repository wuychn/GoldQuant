"""IC 驱动的因子权重：ICIR 比例权重 + BH-FDR 多重检验。

权重 ∝ 截面 IC 的信息比（ICIR = ic_mean / ic_std），仅保留 IC 显著为正的因子；
负 IC/噪声因子权重 0（在 ``compose_alpha`` 中被 ``w<=0`` 跳过）。

为控制 16 因子单因子 t 检验的多重比较假阳性，对全因子 IC 的 t→p 做
Benjamini-Hochberg FDR 校正（``fdr_alpha``），未通过校正的因子权重置 0——
替代旧的"单因子 t>2"硬阈值（假阳性远超名义水平）。

direction 已在面板预乘（``panel_builder``），权重保持非负。``compose_alpha`` 是
z-score 加权均值，对权重绝对尺度不敏感 → ICIR 比例权重可直接用。
"""

from __future__ import annotations

import numpy as np


def _tstat_to_pvalue(tstat: float, n_days: int) -> float:
    """IC>0 单侧 t 检验 p 值（n_days 为 IC 截面日数，df=n_days-1）。"""
    if not n_days or n_days <= 1 or tstat is None:
        return 1.0
    from scipy.stats import t as tdist

    return float(tdist.sf(abs(float(tstat)), max(1, n_days - 1)))


def select_factors_fdr(ic_report: dict, *, fdr_alpha: float = 0.05) -> set[str]:
    """BH-FDR：对全因子 IC 的 t→p 做 Benjamini-Hochberg 校正，返回通过因子集。

    纯 numpy 实现（无 statsmodels 依赖）：p 升序排列，找最大 k 使 p_(k) ≤ k/n·α，
    拒绝 p_(1)..p_(k) 对应假设。
    """
    ps: list[float] = []
    names: list[str] = []
    for name, m in (ic_report or {}).items():
        if not isinstance(m, dict):
            continue
        t = m.get("t_stat")
        n = m.get("n_days", 0)
        if t is None:
            continue
        ps.append(_tstat_to_pvalue(float(t), int(n or 0)))
        names.append(name)
    if not ps:
        return set()
    p = np.array(ps, dtype=float)
    n = len(p)
    order = np.argsort(p)
    sorted_p = p[order]
    k_max = -1
    for i in range(n):
        if sorted_p[i] <= (i + 1) / n * fdr_alpha:
            k_max = i
    if k_max < 0:
        return set()
    return {names[int(order[i])] for i in range(k_max + 1)}


def icir_weights(
    ic_report: dict,
    *,
    min_icir: float = 0.0,
    min_ic_mean: float = 0.0,
    min_tstat: float = 2.0,
) -> dict[str, float]:
    """从 IC 报告产出 {factor: weight}（通过 ICIR/IC 均值/t 阈值的因子）。

    注意：ICIR 比例权重未做因子间去相关；共线诊断见 ``factor_correlation_matrix``。
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
        if ic_mean < min_ic_mean or icir < min_icir or tstat < min_tstat:
            continue
        out[name] = icir if icir > 0 else 0.0
    return out


def full_weight_map(
    ic_report: dict,
    all_factors: list[str],
    *,
    min_icir: float = 0.0,
    min_ic_mean: float = 0.0,
    min_tstat: float = 2.0,
    floor: float = 0.0,
    fdr_alpha: float | None = None,
) -> dict[str, float]:
    """完整权重表：ICIR 阈值取权重，其余 0（供 compose_alpha 全覆盖）。

    ``fdr_alpha`` 给定时叠加 BH-FDR 多重检验校正（推荐 ``fit_weights`` 传 0.05，
    控制 16 因子单因子 t 检验的多重比较假阳性）；``None`` 关闭（向后兼容）。
    """
    positive = icir_weights(
        ic_report, min_icir=min_icir, min_ic_mean=min_ic_mean, min_tstat=min_tstat
    )
    if fdr_alpha is not None:
        passed = select_factors_fdr(ic_report, fdr_alpha=fdr_alpha)
        positive = {k: v for k, v in positive.items() if k in passed}
    out = {name: floor for name in all_factors}
    for name, w in positive.items():
        if name in out:
            out[name] = max(floor, w)
    return out
