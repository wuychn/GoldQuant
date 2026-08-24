"""灵活趋势质量策略：买强趋势+质量过滤；趋势走坏强制卖；机会成本可换仓。

与「反转买最差」对立：只做确定性更高的右侧/顺势。
与「每 N 日整体换仓」对立：每日评估，可单票换出/换入。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant.data.universe import _is_st_name
from quant.portfolio.trend_state import trend_fail_streak


@dataclass(frozen=True)
class FlexTrendParams:
    ma_fast: int = 20
    ma_slow: int = 60
    mom_lookback: int = 60
    # 距近 lookback 高点回撤上限（过深=下跌中继，不买）
    max_pullback_from_high: float = 0.12
    # 效率比下限：|净涨|/路径长度，过滤震荡垃圾
    min_eff_ratio: float = 0.15
    eff_lookback: int = 40
    # 近 bounce 日不能崩（避免刚破位）
    min_mom_20: float = -0.05
    # 流动性：近 20 日 amount 均值
    min_adv: float = 1.0e8
    adv_lookback: int = 20
    exclude_st: bool = True
    # 趋势强制卖
    trail_atr_mult: float = 3.0
    trend_fail_days: int = 2
    atr_period: int = 14
    # 机会成本换仓：分数差 ≥ δ_σ × σ(score)；极大则几乎不换
    delta_sigma: float = 0.5
    # 可换出区：持仓排名差于 keep_rank 才允许被换
    keep_rank: int = 8
    max_stocks: int = 3
    full_invest: float = 0.95
    max_weight: float = 0.40
    # 指数环境：仅当 regime_code 收盘≥其 MA 时允许新开仓/换入（已有仓仍可按趋势卖）
    regime_code: str | None = "000300"
    regime_ma: int = 60
    # 反追高：近 20 日涨幅上限
    max_mom_20: float = 0.25


@dataclass
class _Pos:
    buy_date: str
    entry_price: float
    highest_close: float


def _eff_ratio(close: np.ndarray, lookback: int) -> float | None:
    if len(close) < lookback + 1:
        return None
    seg = close[-(lookback + 1) :]
    if not np.all(np.isfinite(seg)) or seg[0] <= 0:
        return None
    net = abs(float(seg[-1] - seg[0]))
    path = float(np.nansum(np.abs(np.diff(seg))))
    if path <= 1e-12:
        return None
    return net / path


def score_row(
    close: np.ndarray,
    high: np.ndarray,
    amount: np.ndarray,
    name: str,
    params: FlexTrendParams,
) -> float | None:
    """单票当日得分；不合格返回 None。"""
    need = max(
        params.ma_slow,
        params.mom_lookback,
        params.eff_lookback,
        params.adv_lookback,
        params.ma_fast + 10,
    ) + 2
    if len(close) < need:
        return None
    if params.exclude_st and _is_st_name(str(name or "")):
        return None
    last = float(close[-1])
    if not np.isfinite(last) or last <= 0:
        return None

    ma_f = float(np.nanmean(close[-params.ma_fast :]))
    ma_s = float(np.nanmean(close[-params.ma_slow :]))
    ma_f_prev = float(np.nanmean(close[-params.ma_fast - 5 : -5]))
    if not (np.isfinite(ma_f) and np.isfinite(ma_s) and np.isfinite(ma_f_prev)):
        return None
    # 趋势：价在双均线之上，且快线上行
    if last < ma_f or last < ma_s or ma_f <= ma_f_prev:
        return None

    # 位置：不能离近期高点太远（不接飞刀）
    hi = float(np.nanmax(high[-params.mom_lookback :]))
    if not np.isfinite(hi) or hi <= 0:
        return None
    pullback = 1.0 - last / hi
    if pullback > params.max_pullback_from_high:
        return None

    # 动量
    c0 = float(close[-params.mom_lookback - 1])
    if not np.isfinite(c0) or c0 <= 0:
        return None
    mom = last / c0 - 1.0
    if mom <= 0:
        return None
    c20 = float(close[-21]) if len(close) > 21 else c0
    mom20 = last / c20 - 1.0 if c20 > 0 else 0.0
    if mom20 < params.min_mom_20:
        return None
    if mom20 > params.max_mom_20:
        return None

    eff = _eff_ratio(close, params.eff_lookback)
    if eff is None or eff < params.min_eff_ratio:
        return None

    adv = float(np.nanmean(amount[-params.adv_lookback :]))
    if not np.isfinite(adv) or adv < params.min_adv:
        return None

    # 综合分：动量 + 效率 + 贴近高点奖励
    near_high = 1.0 - pullback  # 越近高点越大
    return float(mom * 100.0 + eff * 20.0 + near_high * 5.0)


def precompute_flex_scores(
    daily: pd.DataFrame,
    dates: list[str],
    params: FlexTrendParams,
) -> dict[str, list[tuple[str, float]]]:
    d = daily.copy()
    if not pd.api.types.is_string_dtype(d["date"]):
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d["code"] = d["code"].astype(str)
    if "name" not in d.columns:
        d["name"] = ""
    if "amount" not in d.columns:
        d["amount"] = 0.0
    date_set = set(dates)
    out: dict[str, list[tuple[str, float]]] = {dt: [] for dt in dates}
    groups = list(d.groupby("code", sort=False))
    n_codes = len(groups)
    for gi, (code, g) in enumerate(groups):
        if (gi + 1) % 500 == 0:
            print(f"  flex precompute {gi+1}/{n_codes}", flush=True)
        g = g.sort_values("date")
        dates_c = g["date"].astype(str).to_numpy()
        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        high = pd.to_numeric(g["high"], errors="coerce").to_numpy(dtype=float)
        amount = pd.to_numeric(g["amount"], errors="coerce").to_numpy(dtype=float)
        names = g["name"].astype(str).to_numpy()
        need = max(params.ma_slow, params.mom_lookback, params.eff_lookback) + 5
        for i in range(need, len(g)):
            dt = dates_c[i]
            if dt not in date_set:
                continue
            s = score_row(
                close[: i + 1],
                high[: i + 1],
                amount[: i + 1],
                str(names[i]),
                params,
            )
            if s is not None:
                out[dt].append((str(code), float(s)))
    for dt in dates:
        out[dt].sort(key=lambda x: -x[1])
    print(f"  flex precompute done days={len(dates)}", flush=True)
    return out


@dataclass
class FlexTrendPolicy:
    """每日：①趋势坏强制卖 ②空位按分数填 ③机会成本换最弱持仓。"""

    daily: pd.DataFrame
    params: FlexTrendParams = field(default_factory=FlexTrendParams)
    score_cache: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    _pos: dict[str, _Pos] = field(default_factory=dict)
    _by_code: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)
    holding_snapshots: dict = field(default_factory=dict)
    last_actions: dict[str, str] = field(default_factory=dict)
    _prepared: bool = False

    @property
    def n(self) -> int:
        return self.params.max_stocks

    def prepare(self, dates: list[str]) -> None:
        d = self.daily
        if not pd.api.types.is_string_dtype(d["date"]):
            d = d.copy()
            d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        d = d.copy()
        d["code"] = d["code"].astype(str)
        self.daily = d
        self._by_code = {
            str(c): g.sort_values("date").reset_index(drop=True) for c, g in d.groupby("code")
        }
        self.score_cache = precompute_flex_scores(d, dates, self.params)
        self._prepared = True

    def _hist(self, code: str, as_of: str) -> pd.DataFrame:
        g = self._by_code.get(str(code))
        if g is None or g.empty:
            return pd.DataFrame()
        return g[g["date"] <= as_of]

    def _sync(self, current: dict[str, float], prices: dict[str, float], as_of: str) -> None:
        held = {c for c, w in (current or {}).items() if w and w > 1e-9}
        for c in list(self._pos.keys()):
            if c not in held:
                self._pos.pop(c, None)
        for c in held:
            px = float(prices.get(c) or 0.0)
            snap = (self.holding_snapshots or {}).get(c) or {}
            if c not in self._pos:
                entry = float(snap.get("cost") or px or 0.0)
                buy = str(snap.get("buy_date") or as_of)[:10]
                self._pos[c] = _Pos(buy_date=buy, entry_price=entry, highest_close=max(entry, px))
            else:
                st = self._pos[c]
                if px > st.highest_close:
                    st.highest_close = px
                if snap.get("cost"):
                    st.entry_price = float(snap["cost"])
                if snap.get("buy_date"):
                    st.buy_date = str(snap["buy_date"])[:10]

    def _regime_ok(self, as_of: str) -> bool:
        code = self.params.regime_code
        if not code:
            return True
        g = self._by_code.get(str(code))
        if g is None or g.empty:
            return True  # 无指数数据则不拦截
        sub = g[g["date"] <= as_of]
        ma_n = self.params.regime_ma
        if len(sub) < ma_n:
            return True
        c = pd.to_numeric(sub["close"], errors="coerce")
        last = float(c.iloc[-1])
        ma = float(c.iloc[-ma_n:].mean())
        return bool(np.isfinite(last) and np.isfinite(ma) and last >= ma)

    def target_weights(self, alpha, prices, current, date):
        as_of = str(date)[:10]
        self.last_actions = {}
        self._sync(current or {}, prices or {}, as_of)
        p = self.params
        ranked = list(self.score_cache.get(as_of) or [])
        score_of = {c: s for c, s in ranked}
        rank_of = {c: i + 1 for i, (c, _) in enumerate(ranked)}
        regime_ok = self._regime_ok(as_of)

        # 1) 趋势强制卖；其余保留
        survivors: list[str] = []
        for code, w in (current or {}).items():
            if not w or w <= 1e-9:
                continue
            st = self._pos.get(code)
            if st is None:
                survivors.append(code)
                continue
            hist = self._hist(code, as_of)
            streak = trend_fail_streak(
                hist,
                buy_date=st.buy_date,
                atr_mult=p.trail_atr_mult,
                ma_period=p.ma_fast,
                atr_period=p.atr_period,
            )
            if streak >= p.trend_fail_days:
                self.last_actions[code] = "trend_force"
                continue
            survivors.append(code)

        held = list(survivors)

        # 弱势环境：只许减仓/持有，不许开新仓或换入
        if regime_ok:
            scores = np.array([s for _, s in ranked], dtype=float) if ranked else np.array([1.0])
            sigma = float(np.nanstd(scores, ddof=1)) if len(scores) > 1 else 1.0
            if sigma < 1e-9:
                sigma = 1.0
            delta = p.delta_sigma * sigma

            def weakest_replaceable(held_list: list[str]) -> str | None:
                cands = []
                for c in held_list:
                    r = rank_of.get(c)
                    if r is None or r > p.keep_rank:
                        cands.append((score_of.get(c, -1e18), c))
                if not cands:
                    return None
                cands.sort()
                return cands[0][1]

            # 2) 机会成本换仓
            for new_code, new_s in ranked:
                if len(held) < p.max_stocks:
                    break
                if new_code in held or new_code not in (prices or {}):
                    continue
                old = weakest_replaceable(held)
                if old is None:
                    break
                if new_s - score_of.get(old, -1e18) < delta:
                    continue
                held.remove(old)
                held.append(new_code)
                self.last_actions[old] = "swap_out"
                self.last_actions[new_code] = "swap_in"

            # 3) 空位
            for code, _s in ranked:
                if len(held) >= p.max_stocks:
                    break
                if code in held or code not in (prices or {}):
                    continue
                held.append(code)
                self.last_actions[code] = self.last_actions.get(code) or "enter"

        held = [c for c in held if c in (prices or {})][: p.max_stocks]
        if not held:
            return {}
        w = min(p.full_invest / len(held), p.max_weight)
        return {c: w for c in held}
