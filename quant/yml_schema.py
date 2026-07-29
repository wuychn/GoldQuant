"""quant.yml Pydantic schema（Phase 4 强校验）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DataSourcesCfg(BaseModel):
    model_config = ConfigDict(extra="ignore")

    spot: str = "akshare"


class DataCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_max_concurrent: int = Field(default=1, ge=1, le=32)
    akshare_max_concurrent: int | None = Field(default=None, ge=1, le=32)
    ths_max_concurrent: int | None = Field(default=None, ge=1, le=32)
    eastmoney_max_concurrent: int | None = Field(default=None, ge=1, le=32)
    sources: DataSourcesCfg = Field(default_factory=DataSourcesCfg)


class SchedulerCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    news_hours: str = "8,9,10,11,12,13,14,15,16,17,18,19,20,21,22"
    news_minute: int = Field(default=0, ge=0, le=59)
    pre_market_time: str = "09:25"
    during_market_times: str = ""
    post_market_lunch_time: str = "11:50"
    maintain_daily_time: str = "16:00"
    daily_decision_time: str = "20:10"
    prefetch_concepts_enabled: bool = True
    prefetch_concepts_time: str = "05:00"
    misfire_grace_sec: int = Field(default=600, ge=60, le=86400)
    in_process_modes: list[str] = Field(
        default_factory=lambda: [
            "news",
            "pre_market",
            "during_market",
            "post_market_lunch",
            "post_market_evening",
        ]
    )


class ResearchFactorsCfg(BaseModel):
    model_config = ConfigDict(extra="ignore")

    strict_oos_weights: bool = True


class ResearchCfg(BaseModel):
    model_config = ConfigDict(extra="ignore")

    factors: ResearchFactorsCfg = Field(default_factory=ResearchFactorsCfg)


class QuantConfigSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gates: dict[str, Any] = Field(default_factory=dict)
    research: ResearchCfg = Field(default_factory=ResearchCfg)
    portfolio: dict[str, Any] = Field(default_factory=dict)
    candidate: dict[str, Any] = Field(default_factory=dict)
    data: DataCfg = Field(default_factory=DataCfg)
    scheduler: SchedulerCfg = Field(default_factory=SchedulerCfg)


def validate_quant_config(cfg: dict) -> list[str]:
    errors: list[str] = []
    try:
        QuantConfigSchema.model_validate(cfg)
    except Exception as e:
        errors.append(str(e))
    return errors
