"""阶段波段：回调企稳买入，阶段转弱卖出，不及预期砍仓，不追高。

语义对齐用户口径（非左侧摸底、非追高、非反转接飞刀）：
- 大级别仍在上升结构中
- 出现过适度回调
- 近端企稳/反弹确认后买入
- 买入后继续破位 → thesis_fail 卖出
- 持仓期冲高后转弱（ATR 回撤）或滞涨 → 阶段结束卖出
- 空位才开新仓；不因「外面更强」踢掉未结束的波段
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls

import numpy as np
import pandas as pd

from quant.data.calendar import trading_days_between
from quant.data.universe import _is_st_name
from quant.exit.atr import atr


@dataclass(frozen=True)
class StageSwingParams:
    # —— 结构（大级别：先有过阶段上涨，再回调）——
    struct_ma: int = 60
    require_above_struct_ma: bool = True
    require_struct_ma_rising: bool = True
    struct_ma_slope_lb: int = 10
    min_stage_rise: float = 0.08  # 波段高相对 60 日前至少涨过 8%
    peak_near_high_pct: float = 0.98  # 近端高点须接近 60 日高（真·阶段高）
    # —— 回调（适度，拒绝过深抄底）——
    swing_lookback: int = 20
    pullback_atr_min: float = 1.0
    pullback_atr_max: float = 2.5
    pullback_sweet_atr: float = 1.5  # 打分甜区：适度回调，不是越深越好
    atr_period: int = 14
    # —— 企稳 ——
    bounce_days: int = 2
    bounce_atr_min: float = 0.35
    require_above_ma20: bool = True
    require_close_up: bool = True  # 当日收涨，企稳确认
    require_break_pullback_high: bool = True  # 收盘突破近端回调高（右侧确认，拒绝假企稳）
    break_lookback: int = 5  # 突破窗口：不含当日的近 N 日最高价
    ma20: int = 20
    # —— 过滤 ——
    exclude_st: bool = True
    min_adv: float = 1.5e8
    adv_lookback: int = 20
    # —— 卖出：不及预期 ——
    fail_window_days: int = 5
    fail_atr_mult: float = 1.0
    # —— 卖出：阶段转弱（冲高后回吐）——
    trail_atr_mult: float = 2.0
    # —— 卖出：滞涨 ——
    stall_hold_days: int = 20
    stall_atr_mult: float = 0.5
    # —— 时间兜底 ——
    time_stop_days: int = 45
    # —— 组合 ——
    max_stocks: int = 3
    full_invest: float = 0.95
    max_weight: float = 0.40


@dataclass
class _Pos:
    buy_date: str
    entry_price: float
    highest_close: float
    swing_low: float  # 入场前回调低点（用于不及预期）


def _last_atr(df: pd.DataFrame, period: int) -> float | None:
    if df is None or len(df) < period + 1:
        return None
    a = float(atr(df, period).iloc[-1])
    return a if np.isfinite(a) and a > 0 else None


def stage_buy_score(df: pd.DataFrame, name: str, params: StageSwingParams) -> float | None:
    """回调企稳得分；不合格 None。

    打分偏好：阶段真高点后的适度回调 + 企稳力度。
    明确不偏好「回调越深越好」（那是左侧/接刀）。
    """
    if df is None or df.empty:
        return None
    if params.exclude_st and _is_st_name(str(name or "")):
        return None
    need = max(
        params.struct_ma + params.struct_ma_slope_lb,
        params.swing_lookback,
        params.ma20,
        params.atr_period,
        params.adv_lookback,
        params.bounce_days,
    ) + 3
    if len(df) < need:
        return None

    close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
    high = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
    amount = (
        pd.to_numeric(df["amount"], errors="coerce").to_numpy(dtype=float)
        if "amount" in df.columns
        else np.zeros(len(df))
    )
    last = float(close[-1])
    a = _last_atr(df, params.atr_period)
    if not np.isfinite(last) or last <= 0 or a is None:
        return None

    adv = float(np.nanmean(amount[-params.adv_lookback :]))
    if not np.isfinite(adv) or adv < params.min_adv:
        return None

    # 大级别结构
    ma_s = float(np.nanmean(close[-params.struct_ma :]))
    ma_s_prev = float(
        np.nanmean(close[-params.struct_ma - params.struct_ma_slope_lb : -params.struct_ma_slope_lb])
    )
    if params.require_above_struct_ma and last < ma_s:
        return None
    if params.require_struct_ma_rising and not (ma_s > ma_s_prev):
        return None

    if params.require_above_ma20:
        ma20 = float(np.nanmean(close[-params.ma20 :]))
        if last < ma20:
            return None

    if params.require_close_up and len(close) >= 2 and not (last > float(close[-2])):
        return None

    # 真·阶段高：近端高点接近 60 日高，且相对 60 日前有过上涨
    peak = float(np.nanmax(high[-params.swing_lookback :]))
    hi60 = float(np.nanmax(high[-params.struct_ma :]))
    base = float(close[-params.struct_ma])
    if not np.isfinite(peak) or peak <= 0 or not np.isfinite(hi60) or not np.isfinite(base) or base <= 0:
        return None
    if peak < hi60 * params.peak_near_high_pct:
        return None
    stage_rise = peak / base - 1.0
    if stage_rise < params.min_stage_rise:
        return None

    pullback = (peak - last) / a
    if pullback < params.pullback_atr_min or pullback > params.pullback_atr_max:
        return None

    # 右侧确认：突破近端回调高（不含当日），避免「反弹一天又砸」
    if params.require_break_pullback_high:
        lb = max(1, int(params.break_lookback))
        if len(high) < lb + 1:
            return None
        ref_high = float(np.nanmax(high[-lb - 1 : -1]))
        if not np.isfinite(ref_high) or last <= ref_high:
            return None

    # 企稳：近 bounce_days 反弹够
    j = len(close) - 1 - params.bounce_days
    if j < 0 or close[j] <= 0 or not np.isfinite(close[j]):
        return None
    bounce = last / float(close[j]) - 1.0
    atr_frac = a / last
    if bounce < params.bounce_atr_min * atr_frac:
        return None

    # 适度回调甜区 + 企稳 ATR 倍数；越深越差
    bounce_atr = bounce / max(atr_frac, 1e-9)
    depth_fit = 1.0 / (1.0 + abs(pullback - params.pullback_sweet_atr))
    return float(bounce_atr * depth_fit * (1.0 + min(stage_rise, 0.35)))


def stage_sell(
    df: pd.DataFrame,
    *,
    entry_price: float,
    highest_close: float,
    swing_low: float,
    hold_days: int,
    params: StageSwingParams,
) -> str | None:
    """返回卖出原因；None=继续持有。"""
    if df is None or df.empty or entry_price <= 0:
        return None
    last = float(pd.to_numeric(df["close"], errors="coerce").iloc[-1])
    if not np.isfinite(last):
        return None
    a = _last_atr(df, params.atr_period)

    # 1) 不及预期：短窗内破成本 ATR 带或破回调低点
    if hold_days <= params.fail_window_days and a is not None:
        if last <= entry_price - params.fail_atr_mult * a:
            return "thesis_fail"
        if swing_low > 0 and last < swing_low * 0.995:
            return "thesis_fail"

    # 2) 阶段转弱：自持仓期高点回撤
    if a is not None and last <= float(highest_close) - params.trail_atr_mult * a:
        return "stage_turn"

    # 3) 滞涨：时间到了还没走出波段利润
    ret = last / entry_price - 1.0
    if hold_days >= params.stall_hold_days and a is not None:
        if ret < params.stall_atr_mult * (a / entry_price):
            return "stall"

    # 4) 时间兜底（浮亏才砍，避免过早下车赢家可放宽：仅浮亏）
    if hold_days >= params.time_stop_days and ret < 0:
        return "time"

    return None


def precompute_stage_buys(
    daily: pd.DataFrame,
    dates: list[str],
    params: StageSwingParams,
) -> dict[str, list[tuple[str, float]]]:
    """按票向量化预计算买入分（避免逐日重算 ATR）。"""
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
    need = max(
        params.struct_ma + params.struct_ma_slope_lb,
        params.swing_lookback,
        params.ma20,
        params.atr_period,
        params.adv_lookback,
        params.bounce_days,
    ) + 3
    for gi, (code, g) in enumerate(groups):
        if (gi + 1) % 500 == 0:
            print(f"  stage precompute {gi+1}/{n_codes}", flush=True)
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < need:
            continue
        name0 = str(g["name"].iloc[-1] if "name" in g.columns else "")
        if params.exclude_st and _is_st_name(name0):
            continue

        close = pd.to_numeric(g["close"], errors="coerce").to_numpy(dtype=float)
        high = pd.to_numeric(g["high"], errors="coerce").to_numpy(dtype=float)
        amount = pd.to_numeric(g["amount"], errors="coerce").to_numpy(dtype=float)
        a_arr = atr(g, params.atr_period).to_numpy(dtype=float)
        cs = pd.Series(close)
        ma_s = cs.rolling(params.struct_ma, min_periods=params.struct_ma).mean().to_numpy()
        ma_s_prev = (
            cs.rolling(params.struct_ma, min_periods=params.struct_ma)
            .mean()
            .shift(params.struct_ma_slope_lb)
            .to_numpy()
        )
        ma20 = cs.rolling(params.ma20, min_periods=params.ma20).mean().to_numpy()
        hs = pd.Series(high)
        peak = hs.rolling(params.swing_lookback, min_periods=1).max().to_numpy()
        hi60 = hs.rolling(params.struct_ma, min_periods=params.struct_ma).max().to_numpy()
        # close[-struct_ma] ≡ close[i - struct_ma + 1] ≡ shift(struct_ma - 1)
        base = cs.shift(params.struct_ma - 1).to_numpy()
        adv = pd.Series(amount).rolling(params.adv_lookback, min_periods=1).mean().to_numpy()
        dates_arr = g["date"].astype(str).to_numpy()
        bd = params.bounce_days
        code_s = str(code)

        for i in range(need - 1, len(g)):
            dt = dates_arr[i]
            if dt not in date_set:
                continue
            last = float(close[i])
            a = float(a_arr[i])
            if not np.isfinite(last) or last <= 0 or not np.isfinite(a) or a <= 0:
                continue
            if float(adv[i]) < params.min_adv:
                continue
            ms = float(ma_s[i])
            msp = float(ma_s_prev[i])
            if params.require_above_struct_ma and (not np.isfinite(ms) or last < ms):
                continue
            if params.require_struct_ma_rising and (
                not np.isfinite(ms) or not np.isfinite(msp) or not (ms > msp)
            ):
                continue
            if params.require_above_ma20:
                m20 = float(ma20[i])
                if not np.isfinite(m20) or last < m20:
                    continue
            if params.require_close_up and not (last > float(close[i - 1])):
                continue
            pk = float(peak[i])
            h60 = float(hi60[i])
            b0 = float(base[i])
            if not np.isfinite(pk) or pk <= 0 or not np.isfinite(h60) or not np.isfinite(b0) or b0 <= 0:
                continue
            if pk < h60 * params.peak_near_high_pct:
                continue
            stage_rise = pk / b0 - 1.0
            if stage_rise < params.min_stage_rise:
                continue
            pullback = (pk - last) / a
            if pullback < params.pullback_atr_min or pullback > params.pullback_atr_max:
                continue
            if params.require_break_pullback_high:
                lb = max(1, int(params.break_lookback))
                if i < lb:
                    continue
                ref_high = float(np.nanmax(high[i - lb : i]))
                if not np.isfinite(ref_high) or last <= ref_high:
                    continue
            j = i - bd
            if j < 0 or close[j] <= 0 or not np.isfinite(close[j]):
                continue
            bounce = last / float(close[j]) - 1.0
            atr_frac = a / last
            if bounce < params.bounce_atr_min * atr_frac:
                continue
            bounce_atr = bounce / max(atr_frac, 1e-9)
            depth_fit = 1.0 / (1.0 + abs(pullback - params.pullback_sweet_atr))
            score = float(bounce_atr * depth_fit * (1.0 + min(stage_rise, 0.35)))
            out[dt].append((code_s, score))
    for dt in dates:
        out[dt].sort(key=lambda x: -x[1])
    print(f"  stage precompute done days={len(dates)}", flush=True)
    return out


def _swing_low_before_entry(hist: pd.DataFrame, lookback: int) -> float:
    if hist is None or hist.empty:
        return 0.0
    low = pd.to_numeric(hist["low"], errors="coerce")
    window = low.iloc[-min(len(low), lookback) :]
    v = float(window.min())
    return v if np.isfinite(v) else 0.0


@dataclass
class StageSwingPolicy:
    daily: pd.DataFrame
    params: StageSwingParams = field(default_factory=StageSwingParams)
    buy_cache: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    _pos: dict[str, _Pos] = field(default_factory=dict)
    _by_code: dict[str, pd.DataFrame] = field(default_factory=dict, repr=False)
    holding_snapshots: dict = field(default_factory=dict)
    last_sell_reasons: dict[str, str] = field(default_factory=dict)
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
        self.buy_cache = precompute_stage_buys(d, dates, self.params)
        self._prepared = True

    def _hist(self, code: str, as_of: str) -> pd.DataFrame:
        g = self._by_code.get(str(code))
        if g is None or g.empty:
            return pd.DataFrame()
        return g[g["date"] <= as_of]

    def _hold_days(self, buy: str, as_of: str) -> int:
        try:
            return int(
                trading_days_between(
                    date_cls.fromisoformat(str(buy)[:10]),
                    date_cls.fromisoformat(str(as_of)[:10]),
                )
            )
        except Exception:
            return 0

    def _sync(self, current, prices, as_of: str) -> None:
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
                hist = self._hist(c, as_of)
                sl = _swing_low_before_entry(hist, self.params.swing_lookback)
                self._pos[c] = _Pos(
                    buy_date=buy,
                    entry_price=entry,
                    highest_close=max(entry, px),
                    swing_low=sl,
                )
            else:
                st = self._pos[c]
                if px > st.highest_close:
                    st.highest_close = px
                if snap.get("cost"):
                    st.entry_price = float(snap["cost"])
                if snap.get("buy_date"):
                    st.buy_date = str(snap["buy_date"])[:10]

    def target_weights(self, alpha, prices, current, date):
        as_of = str(date)[:10]
        self.last_sell_reasons = {}
        self._sync(current or {}, prices or {}, as_of)
        p = self.params

        # 1) 评估卖出：波段未结束则保留（不因外部更强被踢）
        keep: dict[str, float] = {}
        for code, w in (current or {}).items():
            if not w or w <= 1e-9:
                continue
            st = self._pos.get(code)
            if st is None:
                keep[code] = float(w)
                continue
            hist = self._hist(code, as_of)
            reason = stage_sell(
                hist,
                entry_price=st.entry_price,
                highest_close=st.highest_close,
                swing_low=st.swing_low,
                hold_days=self._hold_days(st.buy_date, as_of),
                params=p,
            )
            if reason is not None:
                self.last_sell_reasons[code] = reason
                continue
            keep[code] = float(w)

        # 2) 空位按回调企稳强度填充
        slots = max(0, p.max_stocks - len(keep))
        new_w: dict[str, float] = {}
        if slots > 0:
            cands = list(self.buy_cache.get(as_of) or [])
            picked = []
            for code, strength in cands:
                if code in keep or code not in (prices or {}):
                    continue
                picked.append((code, strength))
                if len(picked) >= slots:
                    break
            if picked:
                budget = max(0.0, p.full_invest - sum(keep.values()))
                w_each = min(budget / len(picked), p.max_weight)
                for code, _ in picked:
                    new_w[code] = w_each

        out = {**keep, **new_w}
        return {c: v for c, v in out.items() if v > 1e-6}
