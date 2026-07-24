"""维度 Ablation：按真实评分权重去掉维度后比较日度 RankIC / ICIR。"""

from __future__ import annotations

from typing import Any, Callable

from quant.config import load_scoring_config
from quant.ml.dataset import ScoreSample, load_score_samples
from quant.research.factor.ic import daily_cross_sectional_rank_ic, factor_report


def run_ablation(
    baseline_fn: Callable[[], dict[str, Any]],
    variants: dict[str, Callable[[], dict[str, Any]]],
) -> dict[str, Any]:
    """对比 baseline 与各变体的 metrics（通用回测 ablation）。"""
    base = baseline_fn()
    out: dict[str, Any] = {"baseline": base, "variants": {}}
    base_ret = float(base.get("total_return", 0) or 0)
    for name, fn in variants.items():
        m = fn()
        delta = float(m.get("total_return", 0) or 0) - base_ret
        out["variants"][name] = {**m, "delta_vs_baseline": round(delta, 4)}
    return out


def _dimension_weights() -> dict[str, float]:
    dims = (load_scoring_config().get("dimensions") or {})
    out: dict[str, float] = {}
    for k, v in dims.items():
        if not isinstance(v, dict) or not v.get("enabled", True):
            continue
        w = float(v.get("weight", 0) or 0)
        if w > 0:
            out[k] = w
    return out


def _reweight_total(
    sample: ScoreSample,
    *,
    exclude: str | None = None,
    weights: dict[str, float] | None = None,
) -> float:
    """按配置权重重算总分；exclude 时去掉该维并对其余维重新归一。"""
    wmap = weights if weights is not None else _dimension_weights()
    nums = 0.0
    dens = 0.0
    for dim, score in sample.dim_scores.items():
        if exclude and dim == exclude:
            continue
        w = float(wmap.get(dim, 0.0) or 0.0)
        if w <= 0:
            # 样本有分、配置无权重时等权兜底该维
            w = 1.0
        nums += float(score) * w
        dens += w
    if dens <= 0:
        return float(sample.total)
    return nums / dens


def _total_without_dim(
    sample: ScoreSample,
    dim: str,
    weights: dict[str, float] | None = None,
) -> float:
    """去掉某维度后按真实权重重算总分。"""
    return _reweight_total(sample, exclude=dim, weights=weights)


def dimension_ablation_ic(
    samples: list[ScoreSample] | None = None,
    *,
    dims: list[str] | None = None,
) -> dict[str, Any]:
    """逐维 ablation：去掉该维后总分的日度 RankIC 变化。"""
    samples = samples if samples is not None else load_score_samples()
    if not samples:
        return {"baseline": {}, "ablations": {}, "reason": "无样本"}

    weights = _dimension_weights()
    baseline = daily_cross_sectional_rank_ic(
        samples,
        score_fn=lambda s: _reweight_total(s, weights=weights),
    )
    all_dims: set[str] = set()
    for s in samples:
        all_dims.update(s.dim_scores.keys())
    target = dims or sorted(all_dims)

    ablations: dict[str, Any] = {}
    for dim in target:
        ic = daily_cross_sectional_rank_ic(
            samples,
            score_fn=lambda s, d=dim: _total_without_dim(s, d, weights),
        )
        ablations[dim] = {
            **ic,
            "delta_ic_mean": round(ic["ic_mean"] - baseline["ic_mean"], 4),
            "delta_icir": round(ic["icir"] - baseline["icir"], 4),
            # delta_ic_mean < 0 表示去掉后 IC 变差 → 该维有正贡献
            "positive_contribution": (ic["ic_mean"] - baseline["ic_mean"]) < -1e-6,
        }

    per_dim = factor_report(samples)
    return {
        "baseline": baseline,
        "ablations": ablations,
        "dimension_ic": per_dim,
        "weights_used": weights,
        "n_samples": len(samples),
    }
