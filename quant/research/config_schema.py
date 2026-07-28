"""quant.yml 结构校验（Tier-1 参数治理）。

r1 评分段（scoring）退役后，仅校验 gates / research 结构。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResearchCfg(BaseModel):
    min_oos_sharpe: float = 0.5
    bootstrap_samples: int = 1000


class QuantConfigSchema(BaseModel):
    model_config = {"extra": "ignore"}

    gates: dict[str, Any] = Field(default_factory=dict)
    research: ResearchCfg = Field(default_factory=ResearchCfg)


def validate_quant_config(cfg: dict) -> list[str]:
    """校验配置，返回错误列表（空=通过）。"""
    errors: list[str] = []
    try:
        QuantConfigSchema(
            gates=cfg.get("gates") or {},
            research=cfg.get("research") or {},
        )
    except Exception as e:
        errors.append(str(e))
    return errors
