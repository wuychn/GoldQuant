"""因子注册表：按名称索引，支持启用/禁用与权重覆盖。"""

from __future__ import annotations

from quant.factors.library import ALL_FACTORS, FactorDef


class FactorRegistry:
    def __init__(self, factors: list[FactorDef] | None = None):
        self._factors = list(factors or ALL_FACTORS)
        self._by_name = {f.name: f for f in self._factors}

    def all(self) -> list[FactorDef]:
        return list(self._factors)

    def names(self) -> list[str]:
        return [f.name for f in self._factors]

    def get(self, name: str) -> FactorDef | None:
        return self._by_name.get(name)

    def enabled(self, names: set[str] | None = None) -> list[FactorDef]:
        if names is None:
            return list(self._factors)
        return [f for f in self._factors if f.name in names]

    def weights(self, overrides: dict[str, float] | None = None) -> dict[str, float]:
        out = {f.name: f.default_weight for f in self._factors}
        if overrides:
            for k, v in overrides.items():
                if k in out:
                    out[k] = float(v)
        return out


REGISTRY = FactorRegistry()
