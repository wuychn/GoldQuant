"""quant.yml 校验。"""

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
    fund_flow_rank_page_size: int | None = Field(default=None, ge=1, le=500)
    fund_flow_rank_max_pages: int | None = Field(default=None, ge=1, le=80)
    req_page_interval: str | None = None
    req_symbol_interval: str | None = None
    req_batch_pause: str | None = None
    req_burst_pages: str | None = None
    sources: DataSourcesCfg = Field(default_factory=DataSourcesCfg)


class SchedulerCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    update_daily_time: str = "16:00"
    maintain_weekly_day: str = "fri"
    maintain_weekly_time: str = "22:00"
    daily_decision_time: str = "20:10"
    misfire_grace_sec: int = Field(default=600, ge=60, le=86400)


class QuantConfigSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gates: dict[str, Any] = Field(default_factory=dict)
    data: DataCfg = Field(default_factory=DataCfg)
    scheduler: SchedulerCfg = Field(default_factory=SchedulerCfg)


def validate_quant_config(cfg: dict) -> list[str]:
    errors: list[str] = []
    try:
        QuantConfigSchema.model_validate(cfg)
    except Exception as e:
        errors.append(str(e))
    return errors
