"""日历冻结算波段：每 N 日按强度重选 TopK，持仓期内不因排名变化换仓。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from quant.swing.signals import SwingBandParams


@dataclass
class CalendarBandPolicy:
    """每 ``rebalance_every`` 个交易日重选；中间冻结目标权重。"""

    daily: pd.DataFrame
    params: SwingBandParams = field(default_factory=SwingBandParams)
    max_stocks: int = 10
    full_invest: float = 0.95
    max_weight: float = 0.15
    rebalance_every: int = 10
    buy_cache: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    # 可选：指数过滤（code 如 000300），收盘 < MA 则空仓
    regime_code: str | None = None
    regime_ma: int = 60
    _dates: list[str] = field(default_factory=list, repr=False)
    _date_i: dict[str, int] = field(default_factory=dict, repr=False)
    _frozen: dict[str, float] = field(default_factory=dict, repr=False)
    _by_code: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)
    _prepared: bool = False
    holding_snapshots: dict = field(default_factory=dict)

    @property
    def n(self) -> int:
        return self.max_stocks

    def prepare(self, dates: list[str]) -> None:
        from quant.swing.precompute import precompute_buy_cache

        d = self.daily
        if not pd.api.types.is_string_dtype(d["date"]):
            d = d.copy()
            d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d["code"] = d["code"].astype(str)
        self.daily = d
        self._by_code = {
            str(c): g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")
        }
        self.buy_cache = precompute_buy_cache(d, dates, self.params)
        self._dates = list(dates)
        self._date_i = {d: i for i, d in enumerate(self._dates)}
        self._prepared = True

    def bind_dates(self, dates: list[str]) -> None:
        self._dates = list(dates)
        self._date_i = {d: i for i, d in enumerate(self._dates)}

    def _regime_ok(self, as_of: str) -> bool:
        if not self.regime_code:
            return True
        g = self._by_code.get(str(self.regime_code))
        if g is None or g.empty:
            # 尝试从 daily 取指数（若库里有）
            d = self.daily
            g = d[d["code"].astype(str) == str(self.regime_code)].sort_values("date")
            self._by_code[str(self.regime_code)] = g
        if g is None or g.empty:
            return True
        sub = g[g["date"] <= as_of]
        if len(sub) < self.regime_ma:
            return True
        c = pd.to_numeric(sub["close"], errors="coerce")
        last = float(c.iloc[-1])
        ma = float(c.iloc[-self.regime_ma :].mean())
        return last >= ma

    def target_weights(self, alpha, prices, current, date):
        as_of = str(date)[:10]
        i = self._date_i.get(as_of)
        if i is None:
            # 未知日：保持冻结
            return {c: w for c, w in self._frozen.items() if c in (prices or {}) and w > 1e-6}

        if not self._regime_ok(as_of):
            self._frozen = {}
            return {}

        do_reb = (i % max(1, int(self.rebalance_every)) == 0) or (not self._frozen)
        if do_reb:
            cands = list(self.buy_cache.get(as_of) or [])
            picked: list[str] = []
            for code, _s in cands:
                if code not in (prices or {}):
                    continue
                picked.append(code)
                if len(picked) >= self.max_stocks:
                    break
            if not picked:
                self._frozen = {}
                return {}
            w = min(self.full_invest / len(picked), self.max_weight)
            self._frozen = {c: w for c in picked}
        # 冻结：只保留仍可交易的代码；权重不漂移重算（简化）
        out = {c: w for c, w in self._frozen.items() if c in (prices or {}) and w > 1e-6}
        return out
