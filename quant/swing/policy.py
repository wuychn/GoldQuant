"""波段组合策略：卖出状态机 + 空位按强度加权开仓。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls

import numpy as np
import pandas as pd

from quant.data.calendar import trading_days_between
from quant.swing.signals import (
    SwingBandParams,
    buy_strength,
    dist_high,
    evaluate_sell,
    passes_position_pctile,
)


@dataclass
class _PosState:
    buy_date: str
    entry_price: float
    highest_close: float


@dataclass
class SwingBandPolicy:
    """实现 run_backtest 所需的 target_weights 协议。"""

    daily: pd.DataFrame
    params: SwingBandParams = field(default_factory=SwingBandParams)
    max_stocks: int = 5
    full_invest: float = 0.95
    max_weight: float = 0.22
    equal_weight_new: bool = True  # v3：新开仓等权，降集中度
    # date -> [(code, strength)]
    buy_cache: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    # 持仓状态（引擎也可通过 holding_snapshots 覆盖买入信息）
    _pos: dict[str, _PosState] = field(default_factory=dict)
    holding_snapshots: dict[str, dict] = field(default_factory=dict)
    last_sell_reasons: dict[str, str] = field(default_factory=dict)
    _by_code: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)
    _prepared: bool = False

    @property
    def n(self) -> int:
        return self.max_stocks

    def prepare(self, dates: list[str]) -> None:
        """预计算每日买入候选（动量+位置分位）。"""
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
        self._prepared = True

    def _hist(self, code: str, as_of: str) -> pd.DataFrame:
        g = self._by_code.get(str(code))
        if g is None or g.empty:
            # fallback
            d = self.daily
            if not pd.api.types.is_string_dtype(d["date"]):
                d = d.copy()
                d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
            g = d[d["code"].astype(str) == str(code)].sort_values("date")
            self._by_code[str(code)] = g
        if g.empty:
            return g
        return g[g["date"] <= as_of]

    def _hold_days(self, buy_date: str, as_of: str) -> int:
        try:
            return int(
                trading_days_between(
                    date_cls.fromisoformat(str(buy_date)[:10]),
                    date_cls.fromisoformat(str(as_of)[:10]),
                )
            )
        except Exception:
            return 0

    def _sync_state(self, current: dict[str, float], prices: dict[str, float], as_of: str) -> None:
        held = {c for c, w in (current or {}).items() if w and w > 1e-9}
        # drop gone
        for c in list(self._pos.keys()):
            if c not in held:
                self._pos.pop(c, None)
        for c in held:
            px = float(prices.get(c) or 0.0)
            snap = (self.holding_snapshots or {}).get(c) or {}
            if c not in self._pos:
                entry = float(snap.get("cost") or px or 0.0)
                buy = str(snap.get("buy_date") or as_of)[:10]
                self._pos[c] = _PosState(buy_date=buy, entry_price=entry, highest_close=max(entry, px))
            else:
                st = self._pos[c]
                if px > st.highest_close:
                    st.highest_close = px
                # 若 broker 有更准的成本/买入日则刷新
                if snap.get("cost"):
                    st.entry_price = float(snap["cost"])
                if snap.get("buy_date"):
                    st.buy_date = str(snap["buy_date"])[:10]

    def target_weights(self, alpha, prices, current, date):
        as_of = str(date)[:10]
        self.last_sell_reasons = {}
        self._sync_state(current or {}, prices or {}, as_of)

        # 1) 卖出
        keep_w: dict[str, float] = {}
        for code, w in (current or {}).items():
            if not w or w <= 1e-9:
                continue
            st = self._pos.get(code)
            if st is None:
                keep_w[code] = float(w)
                continue
            hist = self._hist(code, as_of)
            hold_days = self._hold_days(st.buy_date, as_of)
            sig = evaluate_sell(
                hist,
                entry_price=st.entry_price,
                highest_close=st.highest_close,
                hold_days=hold_days,
                params=self.params,
            )
            if sig is not None:
                self.last_sell_reasons[code] = sig.reason
                continue
            keep_w[code] = float(w)

        # 2) 空位
        slots = max(0, self.max_stocks - len(keep_w))
        new_w: dict[str, float] = {}
        if slots > 0:
            cands = list(self.buy_cache.get(as_of) or [])
            if not cands and not self._prepared:
                # 慢路径：现场算（仅测试小样本）
                cands = self._scan_buys_live(as_of, prices or {})
            picked: list[tuple[str, float]] = []
            for code, strength in cands:
                if code in keep_w:
                    continue
                if code not in (prices or {}):
                    continue
                picked.append((code, strength))
                if len(picked) >= slots:
                    break
            if picked:
                budget = max(0.0, self.full_invest - sum(keep_w.values()))
                if self.equal_weight_new:
                    w_each = min(budget / len(picked), self.max_weight)
                    for code, _strength in picked:
                        new_w[code] = w_each
                else:
                    ssum = sum(max(s, 1e-9) for _, s in picked)
                    for code, strength in picked:
                        w = budget * (max(strength, 1e-9) / ssum)
                        new_w[code] = min(w, self.max_weight)

        out = {**keep_w, **new_w}
        # 裁剪超限
        if len(out) > self.max_stocks:
            # 优先保留已持仓
            kept_codes = list(keep_w.keys())
            extras = [c for c in out if c not in keep_w]
            extras.sort(key=lambda c: -new_w.get(c, 0))
            allow = set(kept_codes + extras[: max(0, self.max_stocks - len(kept_codes))])
            out = {c: out[c] for c in allow}
        return {c: v for c, v in out.items() if v > 1e-6}

    def _scan_buys_live(self, as_of: str, prices: dict[str, float]) -> list[tuple[str, float]]:
        dists: list[float] = []
        feats: list[tuple[str, float, float]] = []
        for code in prices:
            hist = self._hist(code, as_of)
            if hist.empty:
                continue
            dh = dist_high(hist, self.params.dist_high_lookback)
            if dh is not None:
                dists.append(float(dh))
            s = buy_strength(hist, self.params)
            if s is not None and dh is not None:
                feats.append((code, float(s), float(dh)))
        out = [
            (c, s)
            for c, s, dh in feats
            if passes_position_pctile(dh, dists, self.params.dist_high_pctile)
        ]
        out.sort(key=lambda x: -x[1])
        return out
