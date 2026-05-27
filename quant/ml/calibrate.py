"""ML 校准入口：读取历史样本，输出阈值/权重建议。

运行方式（手动，非自动）::

    python -m quant.ml calibrate --method grid --apply

输出文件：~/.quant/config/ml_calibration.yml
生效：apply: true 时，下次 load_scoring_config / load_gates_config 合并进 quant.yml 对应段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from quant.config import load_gates_config, load_scoring_config, reload_config_cache
from quant.ml.dataset import load_score_samples
from quant.ml.optimizers import (
    optimize_bayesian,
    optimize_confirmation_intervals,
    optimize_thresholds_grid,
    optimize_weights_lightgbm,
    optimize_weights_linear,
)
from quant.store.paths import config_file, ensure_layout


@dataclass
class CalibrationResult:
    """一次校准的结构化结果，可序列化为 ml_calibration.yml。"""

    method: str
    sample_count: int
    generated_at: str
    thresholds: dict[str, float] = field(default_factory=dict)
    dimension_weights: dict[str, float] = field(default_factory=dict)
    confirmation: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

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


def calibrate(method: str = "grid", *, min_samples: int = 20) -> CalibrationResult:
    """执行离线校准。

    Parameters
    ----------
    method : grid | linear | lightgbm | bayesian
    min_samples : 样本少于该值时不优化，仅返回提示与当前阈值
    """
    ensure_layout()
    cfg = load_scoring_config()
    gates_cfg = load_gates_config()
    samples = load_score_samples(min_samples=min_samples)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = CalibrationResult(method=method, sample_count=len(samples), generated_at=now)

    if len(samples) < min_samples:
        result.notes.append(
            f"历史样本仅 {len(samples)} 条，少于 {min_samples}，建议多运行若干交易日后再校准。"
        )
        result.thresholds = _base_thresholds(cfg)
        return result

    base_th = _base_thresholds(cfg)
    dim_keys = _dim_keys(cfg)
    base_w = _base_weights(cfg)

    if method == "grid":
        opt = optimize_thresholds_grid(samples, base=base_th)
        result.thresholds = {
            "watchlist_threshold": opt["watchlist_threshold"],
            "buy_threshold": opt["buy_threshold"],
            "sell_threshold": opt["sell_threshold"],
        }
        result.metrics = {k: opt[k] for k in ("f1", "precision", "recall", "score") if k in opt}
    elif method == "linear":
        wopt = optimize_weights_linear(samples, dim_keys=dim_keys, base_weights=base_w)
        result.dimension_weights = wopt.get("weights") or base_w
        result.metrics = {"coef": wopt.get("coef"), "intercept": wopt.get("intercept")}
        if wopt.get("note"):
            result.notes.append(str(wopt["note"]))
        gopt = optimize_thresholds_grid(samples, base=base_th)
        result.thresholds = {
            "watchlist_threshold": gopt["watchlist_threshold"],
            "buy_threshold": gopt["buy_threshold"],
            "sell_threshold": gopt["sell_threshold"],
        }
    elif method == "lightgbm":
        wopt = optimize_weights_lightgbm(samples, dim_keys=dim_keys, base_weights=base_w)
        result.dimension_weights = wopt.get("weights") or base_w
        if wopt.get("note"):
            result.notes.append(str(wopt["note"]))
        gopt = optimize_thresholds_grid(samples, base=base_th)
        result.thresholds = {
            "watchlist_threshold": gopt["watchlist_threshold"],
            "buy_threshold": gopt["buy_threshold"],
            "sell_threshold": gopt["sell_threshold"],
        }
    elif method == "bayesian":
        opt = optimize_bayesian(samples, base=base_th)
        result.thresholds = {
            "watchlist_threshold": opt["watchlist_threshold"],
            "buy_threshold": opt["buy_threshold"],
            "sell_threshold": opt["sell_threshold"],
        }
        result.metrics = {k: opt.get(k) for k in ("f1", "method_detail")}
    else:
        raise ValueError(f"未知校准方法: {method}，可选 grid|linear|lightgbm|bayesian")

    result.confirmation = optimize_confirmation_intervals(samples, base_cfg=gates_cfg)
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
