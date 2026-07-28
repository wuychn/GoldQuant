"""回测主引擎：日频循环。

每个交易日 T：
1. 取 universe(T) 当日行情 + 前一日收盘
2. 算 alpha（由调用方提供 alpha_fn 或预构建面板）
3. target_weights = policy.target_weights(alpha, prices, current, T)
4. 差分 → 买卖单 → broker 撮合（T 日收盘成交）
5. broker.record_equity(T, prices)
6. broker.end_of_day()（释放 T+1 锁）

注意：T 日收盘决策 + T 日收盘成交构成乐观偏差（实盘收盘前无法得知收盘价）。
本实现保留该口径作为「理想上界」基准；严格回测应改 T-1 信号 / T 开盘成交（见
``strict_signals`` 参数，启用后 alpha_fn 用 T-1 数据、成交用 T 开盘）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from quant.backtest.broker import SimBroker
from quant.backtest.policy import PortfolioPolicy
from quant.backtest.tradability import shares_for_amount
from quant.data.calendar import to_iso
from quant.exit.rules import (
    DEFAULT_ATR_MULT,
    DEFAULT_ATR_MULT_STOP,
    DEFAULT_HARD_PCT,
    DEFAULT_MAX_HOLD_DAYS,
    evaluate_exits,
)
from quant.exit.state import ExitTracker


AlphaFn = Callable[[str, dict[str, dict]], dict[str, float]]


@dataclass
class ExitConfig:
    """出场参数；为 None 时禁用对应规则。"""

    atr_mult: float = DEFAULT_ATR_MULT
    atr_mult_stop: float = DEFAULT_ATR_MULT_STOP
    hard_pct: float = DEFAULT_HARD_PCT
    max_hold_days: int = DEFAULT_MAX_HOLD_DAYS
    calendar_fn: Callable[[str, str], int] | None = None

    def __post_init__(self) -> None:
        if self.calendar_fn is None:
            # 默认用交易日计数
            from datetime import date as _date

            from quant.data.calendar import trading_days_between, to_iso

            def _cal(a: str, b: str) -> int:
                da = _date.fromisoformat(to_iso(a))
                db = _date.fromisoformat(to_iso(b))
                return trading_days_between(da, db)

            self.calendar_fn = _cal


def _normalize_daily(daily: pd.DataFrame) -> pd.DataFrame:
    """确保 daily['date'] 为 ISO 字符串。"""
    if daily.empty:
        return daily
    if not pd.api.types.is_string_dtype(daily["date"]):
        daily = daily.copy()
        daily["date"] = pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d")
    else:
        sample = str(daily["date"].iloc[0]) if len(daily) else ""
        if len(sample) == 8 and sample.isdigit():
            daily = daily.copy()
            daily["date"] = pd.to_datetime(daily["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    return daily


def _build_prev_close_index(daily: pd.DataFrame) -> dict[str, dict[str, float]]:
    """预计算 {code: {date_iso: prev_close}}，避免每日全表扫描。

    prev_close = 该 code 在 as_of 之前最近一日的收盘。
    """
    out: dict[str, dict[str, float]] = {}
    if daily.empty:
        return out
    for code, g in daily.groupby("code"):
        g = g.sort_values("date").reset_index(drop=True)
        closes = pd.to_numeric(g["close"], errors="coerce")
        dates = g["date"].astype(str).tolist()
        m: dict[str, float] = {}
        prev = None
        for i, d in enumerate(dates):
            if prev is not None and np.isfinite(prev):
                m[d] = float(prev)
            prev = float(closes.iloc[i]) if np.isfinite(closes.iloc[i]) else prev
        out[code] = m
    return out


def _code_history(daily_by_code: dict[str, pd.DataFrame], code: str, as_of: str) -> pd.DataFrame:
    sub = daily_by_code.get(code)
    if sub is None or sub.empty:
        return sub if sub is not None else pd.DataFrame()
    return sub[sub["date"] <= as_of]


def _code_history_before(daily_by_code: dict[str, pd.DataFrame], code: str, as_of: str) -> pd.DataFrame:
    """严格截断：< as_of（不含 as_of 当日 K 线），供 strict 出场避免 T 日前视。"""
    sub = daily_by_code.get(code)
    if sub is None or sub.empty:
        return sub if sub is not None else pd.DataFrame()
    return sub[sub["date"] < as_of]


def _adv_vol_for(daily_by_code: dict[str, pd.DataFrame], code: str, as_of: str) -> tuple[float, float]:
    """截至 as_of 之前（< as_of）的 20 日均成交额（元）与 14 日日波动（%）。PIT，供回测滑点冲击。"""
    sub = daily_by_code.get(code)
    if sub is None or sub.empty:
        return 0.0, 0.0
    prev = sub[sub["date"] < as_of].sort_values("date")
    if prev.empty:
        return 0.0, 0.0
    adv = 0.0
    amt = pd.to_numeric(prev["amount"], errors="coerce").dropna().tail(20)
    if not amt.empty:
        adv = float(amt.mean())
    vol = 0.0
    closes = pd.to_numeric(prev["close"], errors="coerce").dropna().tail(15)
    if len(closes) >= 2:
        rets = closes.pct_change().dropna()
        if len(rets) >= 2:
            vol = float(rets.std() * 100.0)
    return adv, vol


def run_backtest(
    *,
    daily: pd.DataFrame,  # columns: code,date,open,high,low,close,volume,...
    dates: list[str],
    alpha_fn: AlphaFn,
    policy: PortfolioPolicy,
    initial_cash: float = 1_000_000.0,
    max_positions: int = 10,
    exit_config: ExitConfig | None = None,
    strict_signals: bool = True,
) -> SimBroker:
    """日频回测。

    默认 ``strict_signals=True``：T-1 信号 / T 开盘成交。传 False 可得收盘理想上界。
    """
    daily = _normalize_daily(daily)
    iso_dates = [to_iso(d) for d in dates]
    prev_close_index = _build_prev_close_index(daily)
    daily_by_code: dict[str, pd.DataFrame] = {
        c: g.sort_values("date") for c, g in daily.groupby("code")
    }

    broker = SimBroker(cash=initial_cash)
    tracker = ExitTracker() if exit_config is not None else None

    for i, d in enumerate(iso_dates):
        day_rows = daily[daily["date"] == d]
        if day_rows.empty:
            continue
        rows_by_code: dict[str, dict] = {}
        prices: dict[str, float] = {}
        open_prices: dict[str, float] = {}
        prev_closes: dict[str, float | None] = {}
        for _, r in day_rows.iterrows():
            code = str(r["code"])
            rows_by_code[code] = r.to_dict()
            prices[code] = float(r["close"])
            open_prices[code] = float(r.get("open") or r["close"])
            prev_closes[code] = prev_close_index.get(code, {}).get(d)

        # 出场检查（先于再平衡）：触发则强制清仓。
        # strict：决策只用 ≤T-1 信息（highest_close 更新到 T-1 收盘、hist 不含 T 日、
        # 成交用 T 开盘），消除"T 收盘才决定止损"的前视。
        forced: set[str] = set()
        if tracker is not None and exit_config is not None:
            for code in list(broker.holdings.keys()):
                h = broker.holdings[code]
                tracker.update(code, prev_closes.get(code) or 0.0)
                st = tracker.get(code)
                if st is None or code not in rows_by_code:
                    continue
                hist_prev = _code_history_before(daily_by_code, code, d)
                if hist_prev is None or hist_prev.empty:
                    continue
                as_of_prev = str(hist_prev["date"].iloc[-1])
                sig = evaluate_exits(
                    hist_prev,
                    entry_price=h.cost_price,
                    highest_close=st.highest_close,
                    buy_date=h.buy_date,
                    as_of=as_of_prev,
                    atr_mult=exit_config.atr_mult,
                    atr_mult_stop=exit_config.atr_mult_stop,
                    hard_pct=exit_config.hard_pct,
                    max_hold_days=exit_config.max_hold_days,
                    calendar_fn=exit_config.calendar_fn,
                )
                if sig is not None:
                    adv, vol = _adv_vol_for(daily_by_code, code, d)
                    ref = open_prices.get(code) if strict_signals else None
                    broker.sell(
                        code,
                        rows_by_code[code],
                        prev_closes.get(code),
                        reason=sig.reason,
                        ref_price=ref,
                        adv_amount=adv,
                        volatility_pct=vol,
                    )
                    if code not in broker.holdings or broker.holdings[code].shares <= 0:
                        tracker.close(code)
                    forced.add(code)

        # 信号日：strict 模式用 T-1，否则用 T
        signal_date = iso_dates[i - 1] if (strict_signals and i > 0) else d
        alpha = alpha_fn(signal_date, rows_by_code)
        if not alpha:
            broker.suspended_codes = set(broker.mark_suspended(rows_by_code))
            broker.record_equity(d, prices, prev_closes)
            broker.end_of_day()
            continue

        # 当前持仓权重。strict：仓位价值用 T 开盘（成交基准），非 strict 用 T 收盘
        price_basis = open_prices if strict_signals else prices
        eq = broker.total_equity(price_basis, prev_closes) or 1.0
        current = {}
        for code, h in broker.holdings.items():
            p = price_basis.get(code) or prev_closes.get(code) or 0.0
            current[code] = p * h.shares / eq

        # date 用 signal_date（strict=T-1）：voltarget 的 realized_vol/协方差取 ≤date，
        # 传 d 会含 T 日收盘 → 前视（暴跌日才缩仓，实盘 T 开盘做不到）
        target = policy.target_weights(alpha, price_basis, current, signal_date)
        # 强制清仓的票不计入目标
        for c in forced:
            target.pop(c, None)
        # 限制持仓数（与 policy.n 协调；二者应一致以避免二次截断破坏 buffer）
        if max_positions and len(target) > max_positions:
            target = dict(sorted(target.items(), key=lambda kv: -kv[1])[:max_positions])

        # 成交价：strict 模式用 T 开盘，否则用 T 收盘。
        # 同一个价格既用于算股数、也传给 broker 成交，避免股数与成交价基准错配。
        fill_price_for = open_prices if strict_signals else prices

        # 先卖后买
        for code in list(broker.holdings.keys()):
            tw = target.get(code, 0.0)
            cur_w = current.get(code, 0.0)
            if tw < cur_w - 1e-6 and code in rows_by_code:
                target_value = tw * eq
                cur_value = price_basis.get(code, 0.0) * broker.holdings[code].shares
                excess = cur_value - target_value
                if excess > 0:
                    ref = fill_price_for.get(code) or price_basis.get(code, 0.0)
                    sell_shares = shares_for_amount(ref, excess)
                    if sell_shares > 0:
                        adv, vol = _adv_vol_for(daily_by_code, code, d)
                        broker.sell(
                            code, rows_by_code[code], prev_closes.get(code),
                            target_shares=sell_shares,
                            ref_price=ref if strict_signals else None,
                            adv_amount=adv,
                            volatility_pct=vol,
                        )
                        if code not in broker.holdings or broker.holdings[code].shares <= 0:
                            if tracker is not None:
                                tracker.close(code)

        # 买入
        eq = broker.total_equity(price_basis, prev_closes) or 1.0
        for code, tw in target.items():
            row = rows_by_code.get(code)
            if row is None:
                continue
            cur_shares = broker.holdings[code].shares if code in broker.holdings else 0
            cur_value = price_basis.get(code, 0.0) * cur_shares
            target_value = tw * eq
            excess = target_value - cur_value
            if excess > 0:
                ref = fill_price_for.get(code) or price_basis.get(code, 0.0)
                adv, vol = _adv_vol_for(daily_by_code, code, d)
                broker.buy(
                    code, row, prev_closes.get(code), excess,
                    ref_price=ref if strict_signals else None,
                    adv_amount=adv,
                    volatility_pct=vol,
                )
                # buy 可能因涨停/停牌/现金不足而未成交，只在真正持仓时同步 tracker；
                # upsert 保证加仓不重置 highest_close（否则跟踪止损失效）
                if tracker is not None and code in broker.holdings:
                    h = broker.holdings[code]
                    tracker.upsert(code, h.cost_price, h.buy_date)

        broker.suspended_codes = set(broker.mark_suspended(rows_by_code))
        broker.record_equity(d, prices, prev_closes)
        broker.end_of_day()

    return broker
