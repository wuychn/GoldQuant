"""ML 校准入口：读取历史样本，输出阈值/权重建议。

运行方式（手动，非自动）::

    python -m quant.ml calibrate --method grid --apply

输出文件：~/.quant/config/ml_calibration.yml
生效：apply: true 时，下次 load_scoring_config / load_gates_config 合并进 quant.yml 对应段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from quant.config import load_gates_config, load_scoring_config, reload_config_cache
from quant.ml.dataset import ScoreSample, load_score_samples
from quant.ml.optimizers import (
    optimize_bayesian,
    optimize_confirmation_intervals,
    optimize_concept_score_weights,
    optimize_thresholds_grid,
    optimize_weights_lightgbm,
    optimize_weights_linear,
)
from quant.ml.validation import walk_forward_validate
from quant.store.paths import config_file, ensure_layout
from quant.timeutil import cn_datetime_str

# auto 模式下：样本数达到该值时用 lightgbm，否则用 linear（仍须 >= min_optimize_samples 才校准）
DEFAULT_LIGHTGBM_MIN_SAMPLES = 300
MIN_OPTIMIZE_SAMPLES = 10


def resolve_calibration_method(
    method: str,
    sample_count: int,
    *,
    lightgbm_min_samples: int = DEFAULT_LIGHTGBM_MIN_SAMPLES,
) -> tuple[str, list[str]]:
    """auto → 按样本量在 linear / lightgbm 间选择；其余方法原样返回。"""
    if method != "auto":
        return method, []
    if sample_count >= lightgbm_min_samples:
        return "lightgbm", [
            f"auto 选择 lightgbm：样本 {sample_count} >= {lightgbm_min_samples}"
        ]
    return "linear", [
        f"auto 选择 linear：样本 {sample_count} < {lightgbm_min_samples}"
    ]


@dataclass
class CalibrationResult:
    """一次校准的结构化结果，可序列化为 ml_calibration.yml。"""

    method: str
    sample_count: int
    generated_at: str
    thresholds: dict[str, float] = field(default_factory=dict)
    dimension_weights: dict[str, float] = field(default_factory=dict)
    confirmation: dict[str, Any] = field(default_factory=dict)
    concept_tracker: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    apply_blocked: bool = False
    walk_forward: dict[str, Any] = field(default_factory=dict)

    def to_yaml_dict(self) -> dict:
        out: dict[str, Any] = {
            "generated_at": self.generated_at,
            "method": self.method,
            "sample_count": self.sample_count,
            "metrics": self.metrics,
            "notes": self.notes,
        }
        if self.thresholds:
            out["thresholds"] = self.thresholds
        if self.dimension_weights:
            out["dimension_weights"] = self.dimension_weights
        if self.confirmation:
            out["confirmation"] = self.confirmation
        if self.concept_tracker:
            out["concept_tracker"] = self.concept_tracker
        if self.walk_forward:
            out["walk_forward"] = self.walk_forward
        out["apply_blocked"] = self.apply_blocked
        return out


def load_merged_scoring_config() -> dict:
    """与 quant.config.load_scoring_config 相同（便于 ML 模块内引用）。"""
    return load_scoring_config()


def _base_thresholds(cfg: dict) -> dict[str, float]:
    return {
        "watchlist_threshold": float(cfg.get("watchlist_threshold", 65)),
        "buy_threshold": float(cfg.get("buy_threshold", 72)),
        "sell_threshold": float(cfg.get("sell_threshold", 45)),
    }


def _dim_keys(cfg: dict) -> list[str]:
    dims = cfg.get("dimensions") or {}
    return [k for k, v in dims.items() if isinstance(v, dict) and v.get("enabled")]


def _base_weights(cfg: dict) -> dict[str, float]:
    dims = cfg.get("dimensions") or {}
    return {
        k: float(v.get("weight", 0))
        for k, v in dims.items()
        if isinstance(v, dict) and v.get("enabled")
    }


def _calibrate_weights_and_thresholds(
    samples: list[ScoreSample],
    *,
    weight_method: str,
    dim_keys: list[str],
    base_w: dict[str, float],
    base_th: dict[str, float],
) -> tuple[dict[str, float], dict[str, float], dict[str, Any], list[str]]:
    """linear / lightgbm：先优化维度权重，再 grid 搜阈值。"""
    notes: list[str] = []
    if weight_method == "linear":
        wopt = optimize_weights_linear(samples, dim_keys=dim_keys, base_weights=base_w)
        metrics: dict[str, Any] = {
            "coef": wopt.get("coef"),
            "intercept": wopt.get("intercept"),
        }
    elif weight_method == "lightgbm":
        wopt = optimize_weights_lightgbm(samples, dim_keys=dim_keys, base_weights=base_w)
        metrics = {"importance": wopt.get("importance")}
    else:
        raise ValueError(f"不支持的权重方法: {weight_method}")

    weights = wopt.get("weights") or base_w
    if wopt.get("note"):
        notes.append(str(wopt["note"]))

    from quant.config import load_quant_config

    objective = str((load_quant_config().get("research") or {}).get("ml_objective", "sharpe"))
    gopt = optimize_thresholds_grid(samples, base=base_th, objective=objective)
    thresholds = {
        "watchlist_threshold": gopt["watchlist_threshold"],
        "buy_threshold": gopt["buy_threshold"],
        "sell_threshold": gopt["sell_threshold"],
    }
    return weights, thresholds, metrics, notes


def _base_concept_score_weights(gates_cfg: dict) -> dict[str, float]:
    ct = gates_cfg.get("concept_tracker") or {}
    sw = ct.get("score_weights") or {}
    return {
        "selection_count": float(sw.get("selection_count", 50)),
        "composite_gain": float(sw.get("composite_gain", 30)),
        "net_fund_flow": float(sw.get("net_fund_flow", 20)),
    }


def _apply_concept_tracker_calibration(
    result: CalibrationResult,
    samples: list[ScoreSample],
    gates_cfg: dict,
) -> None:
    base_sw = _base_concept_score_weights(gates_cfg)
    opt = optimize_concept_score_weights(samples, base_weights=base_sw)
    sw = opt.get("score_weights") or base_sw
    result.concept_tracker = {"score_weights": sw}
    if opt.get("note"):
        result.notes.append(str(opt["note"]))
    elif "concept_theme_corr" in opt:
        result.metrics["concept_theme_corr"] = opt["concept_theme_corr"]
        result.metrics["concept_proxy_f1"] = opt.get("proxy_f1")
        result.notes.append(
            f"concept 指标权重校准：selection={sw['selection_count']}, "
            f"gain={sw['composite_gain']}, fund={sw['net_fund_flow']}"
        )


def calibrate(
    method: str = "grid",
    *,
    min_samples: int = 100,
    lightgbm_min_samples: int = DEFAULT_LIGHTGBM_MIN_SAMPLES,
) -> CalibrationResult:
    """执行离线校准。

    Parameters
    ----------
    method : grid | linear | lightgbm | bayesian | auto
        auto 时样本 < lightgbm_min_samples 用 linear，否则用 lightgbm（均含阈值 grid）
    min_samples : 样本少于该值时仍输出建议值，但禁止 apply
    lightgbm_min_samples : auto 模式下启用 lightgbm 的样本下限
    """
    ensure_layout()
    cfg = load_scoring_config()
    gates_cfg = load_gates_config()
    samples = load_score_samples(min_samples=min_samples)
    now = cn_datetime_str()
    result = CalibrationResult(method=method, sample_count=len(samples), generated_at=now)
    samples_insufficient_for_apply = len(samples) < min_samples

    if len(samples) < MIN_OPTIMIZE_SAMPLES:
        result.notes.append(
            f"历史样本仅 {len(samples)} 条，少于 {MIN_OPTIMIZE_SAMPLES}，无法优化，保留当前配置。"
        )
        result.thresholds = _base_thresholds(cfg)
        if method in ("auto", "linear", "lightgbm"):
            result.dimension_weights = _base_weights(cfg)
        result.apply_blocked = True
        return result

    if samples_insufficient_for_apply:
        result.notes.append(
            f"历史样本 {len(samples)} 条，少于 apply 门槛 {min_samples}；"
            "以下为参考建议值，禁止写入 quant.yml。"
        )
        result.apply_blocked = True

    resolved, auto_notes = resolve_calibration_method(
        method,
        len(samples),
        lightgbm_min_samples=lightgbm_min_samples,
    )
    result.notes.extend(auto_notes)
    if method == "auto":
        result.method = resolved
        result.metrics["method_requested"] = "auto"

    base_th = _base_thresholds(cfg)
    dim_keys = _dim_keys(cfg)
    base_w = _base_weights(cfg)

    from quant.config import load_quant_config

    objective = str((load_quant_config().get("research") or {}).get("ml_objective", "sharpe"))

    if resolved == "grid":
        opt = optimize_thresholds_grid(samples, base=base_th, objective=objective)
        result.thresholds = {
            "watchlist_threshold": opt["watchlist_threshold"],
            "buy_threshold": opt["buy_threshold"],
            "sell_threshold": opt["sell_threshold"],
        }
        result.metrics = {
            k: opt[k]
            for k in (
                "f1",
                "precision",
                "recall",
                "score",
                "portfolio_sharpe",
                "portfolio_calmar",
                "objective",
            )
            if k in opt
        }
    elif resolved in ("linear", "lightgbm"):
        weights, thresholds, metrics, notes = _calibrate_weights_and_thresholds(
            samples,
            weight_method=resolved,
            dim_keys=dim_keys,
            base_w=base_w,
            base_th=base_th,
        )
        result.dimension_weights = weights
        result.thresholds = thresholds
        result.metrics = {**result.metrics, **metrics}
        result.notes.extend(notes)
    elif resolved == "bayesian":
        opt = optimize_bayesian(samples, base=base_th, objective=objective)
        result.thresholds = {
            "watchlist_threshold": opt["watchlist_threshold"],
            "buy_threshold": opt["buy_threshold"],
            "sell_threshold": opt["sell_threshold"],
        }
        result.metrics = {
            k: opt.get(k)
            for k in ("f1", "method_detail", "score", "objective", "portfolio_sharpe")
        }
    else:
        raise ValueError(
            f"未知校准方法: {method}，可选 grid|linear|lightgbm|bayesian|auto"
        )

    result.confirmation = optimize_confirmation_intervals(samples, base_cfg=gates_cfg)
    _apply_concept_tracker_calibration(result, samples, gates_cfg)

    wf = walk_forward_validate(samples, base_thresholds=result.thresholds or base_th)
    result.walk_forward = wf
    result.metrics = {**result.metrics, "walk_forward": wf}
    if not wf.get("passed"):
        result.apply_blocked = True
        reason = wf.get("reason") or "walk-forward 未通过"
        result.notes.append(f"禁止 apply：{reason}")
    elif samples_insufficient_for_apply:
        result.apply_blocked = True

    return result


def write_calibration(result: CalibrationResult, *, apply: bool = True) -> Path:
    """写入 ~/.quant/config/ml_calibration.yml。"""
    ensure_layout()
    path = config_file("ml_calibration.yml")
    payload = result.to_yaml_dict()
    payload["apply"] = apply
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)
    return path


def clear_scoring_cache() -> None:
    reload_config_cache()
