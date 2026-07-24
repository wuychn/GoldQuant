"""因子契约与截面行。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class FactorSpec:
    """单个原始因子定义。"""

    name: str
    description: str
    compute: Callable[[dict], float | None]
    # 中性化时是否纳入；合成权重默认等权时可被覆盖
    default_weight: float = 1.0


@dataclass
class FactorRow:
    """单日单票因子行。"""

    date: str
    code: str
    name: str = ""
    industry: str = ""
    log_mcap: float | None = None
    raw: dict[str, float] = field(default_factory=dict)
    neutral: dict[str, float] = field(default_factory=dict)
    forward_return_pct: float | None = None  # 持有期收益(%)
    meta: dict[str, Any] = field(default_factory=dict)
