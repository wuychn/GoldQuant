"""SwapGate：值不值得换——分数差 + ADV 成本双门槛。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from quant.execution.sim_rules import TradeSimConfig, load_trade_sim_config
from quant.execution.slippage import SlippageContext, effective_slippage_pct
from quant.portfolio.trend_state import trend_fail_streak, trend_ok


@dataclass
class SwapGateConfig:
    enabled: bool = True
    n_enter: int = 3
    n_exit: int = 8
    max_stocks: int = 3
    atr_band_mult: float = 3.0
    ma_period: int = 20
    atr_period: int = 14
    trend_fail_days: int = 2
    delta_sigma: float = 0.3
    cost_cover_k: float = 2.0
    # 每 1σ alpha 差距对应的预期收益（持有期）
    alpha_to_ret: float = 0.01
    # 单票名义占组合比例（估成本用）；默认 full_invest/max_stocks
    name_weight: float = 0.0
    full_invest: float = 0.95

    @classmethod
    def from_mapping(cls, raw: dict | None) -> "SwapGateConfig":
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            n_enter=int(raw.get("n_enter", 3)),
            n_exit=int(raw.get("n_exit", 8)),
            max_stocks=int(raw.get("max_stocks", 3)),
            atr_band_mult=float(raw.get("atr_band_mult", 3.0)),
            ma_period=int(raw.get("ma_period", 20)),
            atr_period=int(raw.get("atr_period", 14)),
            trend_fail_days=int(raw.get("trend_fail_days", 2)),
            delta_sigma=float(raw.get("delta_sigma", 0.3)),
            cost_cover_k=float(raw.get("cost_cover_k", 2.0)),
            alpha_to_ret=float(raw.get("alpha_to_ret", 0.01)),
            full_invest=float(raw.get("full_invest", 0.95)),
        )


@dataclass
class SwapDecision:
    codes: list[str]
    force_exit: set[str] = field(default_factory=set)
    labels: dict[str, str] = field(default_factory=dict)  # code -> keep|replaceable|force_exit
    swaps: list[tuple[str, str]] = field(default_factory=list)  # (out, in)


def _sigma_alpha(alpha: dict[str, float]) -> float:
    vals = np.array(list(alpha.values()), dtype=float)
    if len(vals) < 2:
        return 1.0
    s = float(np.nanstd(vals, ddof=1))
    return s if s > 1e-12 else 1.0


def estimate_one_way_cost_frac(
    *,
    price: float,
    notional: float,
    adv_amount: float,
    volatility_pct: float = 2.0,
    is_buy: bool,
    code: str = "",
    sim: TradeSimConfig | None = None,
) -> float:
    """单边成本占名义本金比例（滑点 + 佣金 + 卖出印花税等，近似）。"""
    from quant.execution.sim_rules import calc_commission, calc_stamp_tax, calc_transfer_fee

    cfg = sim or load_trade_sim_config()
    if price <= 0 or notional <= 0:
        return 0.0
    shares = max(int(notional / price / 100) * 100, 100)
    amount = price * shares
    part = (amount / adv_amount) if adv_amount > 0 else 0.0
    ctx = SlippageContext(
        volatility_pct=volatility_pct,
        amount=amount,
        adv_amount=float(adv_amount or 0.0),
        participation=part,
    )
    slip = effective_slippage_pct(cfg, ctx)
    fee = calc_commission(amount, cfg) + calc_transfer_fee(amount, code, cfg)
    if not is_buy:
        fee += calc_stamp_tax(amount, side="sell", cfg=cfg)
    return float(slip) + float(fee) / max(amount, 1.0)


def estimate_round_trip_cost_frac(
    *,
    sell_code: str,
    buy_code: str,
    sell_price: float,
    buy_price: float,
    notional: float,
    sell_adv: float,
    buy_adv: float,
    sell_vol: float = 2.0,
    buy_vol: float = 2.0,
    sim: TradeSimConfig | None = None,
) -> float:
    return estimate_one_way_cost_frac(
        price=sell_price,
        notional=notional,
        adv_amount=sell_adv,
        volatility_pct=sell_vol,
        is_buy=False,
        code=sell_code,
        sim=sim,
    ) + estimate_one_way_cost_frac(
        price=buy_price,
        notional=notional,
        adv_amount=buy_adv,
        volatility_pct=buy_vol,
        is_buy=True,
        code=buy_code,
        sim=sim,
    )


def _hist_for(
    daily: pd.DataFrame,
    code: str,
    as_of: str,
) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame()
    d = daily
    if not pd.api.types.is_string_dtype(d["date"]):
        d = d.copy()
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    sub = d[(d["code"].astype(str) == str(code)) & (d["date"] <= as_of)].sort_values("date")
    return sub


def label_holdings(
    alpha: dict[str, float],
    current: dict[str, float],
    *,
    daily: pd.DataFrame,
    as_of: str,
    buy_dates: dict[str, str] | None = None,
    cfg: SwapGateConfig,
) -> dict[str, str]:
    """返回 code -> keep|replaceable|force_exit。"""
    ranked = sorted(alpha.items(), key=lambda kv: -kv[1])
    rank_of = {c: i + 1 for i, (c, _) in enumerate(ranked)}
    buy_dates = buy_dates or {}
    out: dict[str, str] = {}
    for code, w in current.items():
        if not w or w <= 1e-9:
            continue
        hist = _hist_for(daily, code, as_of)
        buy = buy_dates.get(code) or (str(hist["date"].iloc[0]) if not hist.empty else as_of)
        streak = trend_fail_streak(
            hist,
            buy_date=buy,
            atr_mult=cfg.atr_band_mult,
            ma_period=cfg.ma_period,
            atr_period=cfg.atr_period,
        )
        if streak >= cfg.trend_fail_days:
            out[code] = "force_exit"
            continue
        r = rank_of.get(code)
        in_zone = r is not None and r <= cfg.n_exit
        hc = float(pd.to_numeric(hist["close"], errors="coerce").max()) if not hist.empty else 0.0
        ok = trend_ok(
            hist,
            highest_close=hc,
            atr_mult=cfg.atr_band_mult,
            ma_period=cfg.ma_period,
            atr_period=cfg.atr_period,
        )
        out[code] = "keep" if (in_zone and ok) else "replaceable"
    return out


def passes_swap_thresholds(
    alpha_new: float,
    alpha_old: float,
    *,
    sigma: float,
    cost_frac: float,
    cfg: SwapGateConfig,
) -> bool:
    gap = float(alpha_new) - float(alpha_old)
    if gap < cfg.delta_sigma * sigma:
        return False
    edge = (gap / sigma) * cfg.alpha_to_ret
    return edge >= cfg.cost_cover_k * max(float(cost_frac), 0.0)


def select_target_codes(
    alpha: dict[str, float],
    current: dict[str, float],
    *,
    daily: pd.DataFrame,
    as_of: str,
    prices: dict[str, float],
    cfg: SwapGateConfig,
    buy_dates: dict[str, str] | None = None,
    adv_by_code: dict[str, float] | None = None,
    vol_by_code: dict[str, float] | None = None,
    equity: float = 1_000_000.0,
    cost_fn: Callable[..., float] | None = None,
    sim: TradeSimConfig | None = None,
) -> SwapDecision:
    """选出目标持仓代码（不含权重）。"""
    labels = label_holdings(
        alpha, current, daily=daily, as_of=as_of, buy_dates=buy_dates, cfg=cfg
    )
    force_exit = {c for c, lab in labels.items() if lab == "force_exit"}
    keep = [c for c, lab in labels.items() if lab == "keep"]
    replaceable = [c for c, lab in labels.items() if lab == "replaceable"]
    replaceable.sort(key=lambda c: alpha.get(c, -1e18))  # 最弱在前

    held_keep = list(keep)
    # force_exit 不进入目标
    active = list(held_keep)
    # 暂留 replaceable，直到被换出或仓位不够
    pending_rep = list(replaceable)

    ranked = sorted(alpha.items(), key=lambda kv: -kv[1])
    sigma = _sigma_alpha(alpha)
    adv_by_code = adv_by_code or {}
    vol_by_code = vol_by_code or {}
    name_w = cfg.name_weight or (cfg.full_invest / max(cfg.max_stocks, 1))
    notional = float(equity) * float(name_w)
    swaps: list[tuple[str, str]] = []

    held_set = set(active) | set(pending_rep)

    def _cost(sell: str | None, buy: str) -> float:
        if cost_fn is not None:
            return float(cost_fn(sell, buy))
        if sell is None:
            return estimate_one_way_cost_frac(
                price=float(prices.get(buy) or 1.0),
                notional=notional,
                adv_amount=float(adv_by_code.get(buy) or 0.0),
                volatility_pct=float(vol_by_code.get(buy) or 2.0),
                is_buy=True,
                code=buy,
                sim=sim,
            )
        return estimate_round_trip_cost_frac(
            sell_code=sell,
            buy_code=buy,
            sell_price=float(prices.get(sell) or 1.0),
            buy_price=float(prices.get(buy) or 1.0),
            notional=notional,
            sell_adv=float(adv_by_code.get(sell) or 0.0),
            buy_adv=float(adv_by_code.get(buy) or 0.0),
            sell_vol=float(vol_by_code.get(sell) or 2.0),
            buy_vol=float(vol_by_code.get(buy) or 2.0),
            sim=sim,
        )

    # 先用 keep + replaceable 填满（replaceable 可被后续换出）
    codes = list(active)
    for c in pending_rep:
        if len(codes) >= cfg.max_stocks:
            break
        codes.append(c)
    pending_rep = [c for c in pending_rep if c in codes]

    # 开仓 / 替换
    for c, a in ranked:
        if c in codes or c in force_exit:
            continue
        rank = next(i + 1 for i, (x, _) in enumerate(ranked) if x == c)
        slots = cfg.max_stocks - len(codes)
        if slots > 0:
            # 空位：第一版只要求排名 ≤ n_enter
            if rank <= cfg.n_enter:
                codes.append(c)
            continue

        # 满仓：只换最弱 replaceable
        reps_in = [x for x in codes if x in pending_rep or labels.get(x) == "replaceable"]
        if not reps_in:
            break
        reps_in.sort(key=lambda x: alpha.get(x, -1e18))
        old = reps_in[0]
        if labels.get(old) == "keep":
            continue
        cost = _cost(old, c)
        if not passes_swap_thresholds(
            a, alpha.get(old, 0.0), sigma=sigma, cost_frac=cost, cfg=cfg
        ):
            continue
        codes.remove(old)
        if old in pending_rep:
            pending_rep.remove(old)
        codes.append(c)
        swaps.append((old, c))

    # 裁到 max_stocks：优先保留 keep，再按 alpha
    if len(codes) > cfg.max_stocks:
        keeps = [c for c in codes if labels.get(c) == "keep"]
        rest = [c for c in codes if c not in keeps]
        rest.sort(key=lambda c: -alpha.get(c, -1e18))
        codes = (keeps + rest)[: cfg.max_stocks]

    return SwapDecision(codes=codes, force_exit=force_exit, labels=labels, swaps=swaps)
