"""quant.yml 结构校验（Tier-1 参数治理）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class DimensionCfg(BaseModel):
    model_config = {"extra": "ignore"}

    enabled: bool = False
    weight: float = 0.0


class ScoringCfg(BaseModel):
    model_config = {"extra": "ignore"}

    watchlist_threshold: float = Field(ge=0, le=100, default=70)
    watchlist_entry_threshold: float = Field(ge=0, le=100, default=72)
    watchlist_exit_threshold: float = Field(ge=0, le=100, default=66)
    buy_threshold: float = Field(ge=0, le=100, default=72)
    sell_threshold: float = Field(ge=0, le=100, default=45)
    dimensions: dict[str, DimensionCfg] = Field(default_factory=dict)

    @field_validator("watchlist_entry_threshold")
    @classmethod
    def entry_ge_exit(cls, v: float, info) -> float:
        exit_t = info.data.get("watchlist_exit_threshold")
        if exit_t is not None and v < exit_t:
            raise ValueError("watchlist_entry_threshold 应 >= watchlist_exit_threshold")
        return v


class SimulationCfg(BaseModel):
    commission_rate: float = Field(ge=0, le=0.01)
    slippage_pct: float = Field(ge=0, le=0.05)
    slippage_model: str = "fixed"


class ResearchCfg(BaseModel):
    min_oos_sharpe: float = 0.5
    bootstrap_samples: int = 1000


class QuantConfigSchema(BaseModel):
    scoring: ScoringCfg
    gates: dict[str, Any] = Field(default_factory=dict)
    research: ResearchCfg = Field(default_factory=ResearchCfg)


TIER1_KEYS = (
    "watchlist_threshold",
    "watchlist_entry_threshold",
    "watchlist_exit_threshold",
    "buy_threshold",
    "sell_threshold",
)


def validate_quant_config(cfg: dict) -> list[str]:
    """校验配置，返回错误列表（空=通过）。"""
    errors: list[str] = []
    try:
        QuantConfigSchema(
            scoring=cfg.get("scoring") or {},
            gates=cfg.get("gates") or {},
            research=cfg.get("research") or {},
        )
    except Exception as e:
        errors.append(str(e))
    scoring = cfg.get("scoring") or {}
    dims = scoring.get("dimensions") or {}
    enabled_weights = [
        float(v.get("weight", 0))
        for v in dims.values()
        if isinstance(v, dict) and v.get("enabled")
    ]
    if enabled_weights and sum(enabled_weights) <= 0:
        errors.append("至少一个 enabled 维度 weight > 0")
    return errors
