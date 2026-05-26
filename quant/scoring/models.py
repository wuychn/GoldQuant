"""评分结果数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DimensionResult:
    """单维度打分结果；available=False 时不参与总分加权。"""

    name: str
    score: float
    weight: float
    enabled: bool
    available: bool = True
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class StockScore:
    """单只股票综合评分；to_dict() 写入 derived/scores_*.json 供 ML 使用。"""

    code: str
    name: str
    total: float
    strategy: str
    dimensions: list[DimensionResult] = field(default_factory=list)
    passed_threshold: bool = False

    def to_dict(self) -> dict:
        return {
            "股票代码": self.code,
            "股票名称": self.name,
            "总分": round(self.total, 2),
            "战法": self.strategy,
            "达标": self.passed_threshold,
            "分项": [
                {
                    "维度": d.name,
                    "得分": round(d.score, 2),
                    "权重": d.weight,
                    "可用": d.available,
                    "详情": d.detail,
                }
                for d in self.dimensions
            ],
        }
