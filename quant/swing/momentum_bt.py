"""动量双槽回测引擎（开盘买、收盘卖，A 股成本）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.swing.momentum import limit_up_th, pick_open_basket, rank_lists


def cn_slot_cost_frac(
    slot_nav: float,
    codes: list[str],
    *,
    side: str,
    commission_rate: float = 1e-4,
    commission_min: float = 5.0,
    stamp_rate: float = 5e-4,
    transfer_rate: float = 1e-5,
) -> float:
    """A股单边成本占该槽市值比例：万一佣金、不免五、卖出印花税、沪市过户费。"""
    n = len(codes)
    if slot_nav <= 0 or n <= 0:
        return 0.0
    per = slot_nav / n
    comm = 0.0
    xfer = 0.0
    for c in codes:
        comm += max(per * commission_rate, commission_min)
        if str(c).startswith("6"):
            xfer += per * transfer_rate
    stamp = slot_nav * stamp_rate if side == "sell" else 0.0
    return (comm + stamp + xfer) / slot_nav


def run_dual_slot(
    *,
    dates: list[str],
    close: pd.DataFrame,
    open_: pd.DataFrame,
    ranks: dict[str, list[str]],
    lu_open: pd.DataFrame,
    bull_lag: pd.Series,
    topn: int,
    max_gap: float | None,
    cost_rt: float,
    hold_days: int = 1,
    n_slots: int = 2,
    extend_if_win: bool = False,
) -> tuple[list[tuple[str, float]], list[dict]]:
    """多槽等权，逐日盯市。入场开盘、出场收盘。默认双槽 50/50。"""
    codes_keep = set(close.columns)
    w = 1.0 / max(n_slots, 1)
    eq = 1.0
    curve: list[tuple[str, float]] = []
    trades: list[dict] = []
    slots: list[dict | None] = [None] * n_slots

    for i, dt in enumerate(dates):
        day_r = 0.0
        for pos in slots:
            if pos is None:
                continue
            hs = pos["hs"]
            o = open_.iloc[i][hs].to_numpy(float)
            last = pos["last_px"]
            with np.errstate(divide="ignore", invalid="ignore"):
                r_on = np.nanmean(o / last - 1.0)
            if np.isfinite(r_on):
                day_r += w * float(r_on)
            pos["last_px"] = o

        if i >= 1 and bool(bull_lag.iloc[i]):
            free = next((s for s, p in enumerate(slots) if p is None), None)
            if free is not None:
                hs = pick_open_basket(
                    ranks, dates[i - 1], i, codes_keep, lu_open, open_, close, max_gap, topn
                )
                if len(hs) >= max(1, (topn + 1) // 2):
                    o0 = open_.iloc[i][hs].to_numpy(float)
                    if np.isfinite(o0).any():
                        day_r -= w * (cost_rt / 2.0)
                        slots[free] = {
                            "hs": hs,
                            "entry_open": o0,
                            "entry_i": i,
                            "last_px": o0,
                            "entry_dt": dt,
                            "extended": False,
                        }

        for s, pos in enumerate(slots):
            if pos is None:
                continue
            hs = pos["hs"]
            cpx = close.iloc[i][hs].to_numpy(float)
            last = pos["last_px"]
            with np.errstate(divide="ignore", invalid="ignore"):
                r_in = np.nanmean(cpx / last - 1.0)
            if np.isfinite(r_in):
                day_r += w * float(r_in)
            pos["last_px"] = cpx
            due = i >= pos["entry_i"] + hold_days
            if due and extend_if_win and not pos.get("extended"):
                with np.errstate(divide="ignore", invalid="ignore"):
                    r_so_far = float(np.nanmean(cpx / pos["entry_open"] - 1.0))
                if np.isfinite(r_so_far) and r_so_far > 0:
                    pos["extended"] = True
                    due = False
            if due:
                with np.errstate(divide="ignore", invalid="ignore"):
                    r_full = float(np.nanmean(cpx / pos["entry_open"] - 1.0))
                day_r -= w * (cost_rt / 2.0)
                trades.append(
                    {
                        "entry": pos["entry_dt"],
                        "exit": dt,
                        "codes": hs,
                        "ret": round(r_full, 4) if np.isfinite(r_full) else None,
                    }
                )
                slots[s] = None

        eq *= 1.0 + day_r
        curve.append((dt, eq))
    return curve, trades


def run_dual_slot_cn(
    *,
    dates: list[str],
    close: pd.DataFrame,
    open_: pd.DataFrame,
    ranks: dict[str, list[str]],
    lu_open: pd.DataFrame,
    bull_lag: pd.Series,
    topn: int,
    max_gap: float | None,
    hold_days: int = 1,
    n_slots: int = 2,
    extend_if_win: bool = False,
    initial_cash: float = 1_000_000.0,
    commission_rate: float = 1e-4,
    commission_min: float = 5.0,
    off_scale: float = 0.0,
    off_ranks: dict[str, list[str]] | None = None,
    off_topn: int | None = None,
    max_idle: int | None = None,
) -> tuple[list[tuple[str, float]], list[dict], dict]:
    """与 run_dual_slot 相同执行，成本按万一佣金+不免五+卖出印花税。"""
    codes_keep = set(close.columns)
    w0 = 1.0 / max(n_slots, 1)
    eq = 1.0
    curve: list[tuple[str, float]] = []
    trades: list[dict] = []
    slots: list[dict | None] = [None] * n_slots
    cost_buy = 0.0
    cost_sell = 0.0
    n_min5 = 0
    n_orders = 0
    idle = 0
    idle_start: str | None = None
    max_cash_streak = 0
    invested_days = 0
    n_off_entries = 0
    cash_streaks: list[dict] = []

    def _charge(codes: list[str], side: str, slot_w: float) -> float:
        nonlocal cost_buy, cost_sell, n_min5, n_orders
        slot_nav = eq * initial_cash * slot_w
        n = max(len(codes), 1)
        per = slot_nav / n if slot_nav > 0 else 0.0
        n_orders += n
        if per * commission_rate < commission_min - 1e-9:
            n_min5 += n
        frac = cn_slot_cost_frac(
            slot_nav,
            codes,
            side=side,
            commission_rate=commission_rate,
            commission_min=commission_min,
        )
        yuan = frac * slot_nav
        if side == "buy":
            cost_buy += yuan
        else:
            cost_sell += yuan
        return slot_w * frac

    for i, dt in enumerate(dates):
        day_r = 0.0
        for pos in slots:
            if pos is None:
                continue
            hs = pos["hs"]
            sw = float(pos.get("w", w0))
            o = open_.iloc[i][hs].to_numpy(float)
            last = pos["last_px"]
            with np.errstate(divide="ignore", invalid="ignore"):
                r_on = np.nanmean(o / last - 1.0)
            if np.isfinite(r_on):
                day_r += sw * float(r_on)
            pos["last_px"] = o

        bull = i >= 1 and bool(bull_lag.iloc[i])
        force = (max_idle is not None) and (idle >= max_idle) and (not bull)
        want = bull or (off_scale > 1e-12) or force
        scale = 1.0 if bull else (off_scale if off_scale > 1e-12 else (0.5 if force else 0.0))
        use_ranks = ranks if bull or off_ranks is None else off_ranks
        use_topn = topn if bull or off_topn is None else off_topn

        if i >= 1 and want and scale > 1e-12:
            free = next((s for s, p in enumerate(slots) if p is None), None)
            if free is not None:
                hs = pick_open_basket(
                    use_ranks, dates[i - 1], i, codes_keep, lu_open, open_, close, max_gap, use_topn
                )
                if len(hs) >= max(1, (use_topn + 1) // 2):
                    o0 = open_.iloc[i][hs].to_numpy(float)
                    if np.isfinite(o0).any():
                        sw = w0 * scale
                        day_r -= _charge(hs, "buy", sw)
                        slots[free] = {
                            "hs": hs,
                            "entry_open": o0,
                            "entry_i": i,
                            "last_px": o0,
                            "entry_dt": dt,
                            "extended": False,
                            "w": sw,
                        }
                        if not bull:
                            n_off_entries += 1

        for s, pos in enumerate(slots):
            if pos is None:
                continue
            hs = pos["hs"]
            sw = float(pos.get("w", w0))
            cpx = close.iloc[i][hs].to_numpy(float)
            last = pos["last_px"]
            with np.errstate(divide="ignore", invalid="ignore"):
                r_in = np.nanmean(cpx / last - 1.0)
            if np.isfinite(r_in):
                day_r += sw * float(r_in)
            pos["last_px"] = cpx
            due = i >= pos["entry_i"] + hold_days
            if due and extend_if_win and not pos.get("extended"):
                with np.errstate(divide="ignore", invalid="ignore"):
                    r_so_far = float(np.nanmean(cpx / pos["entry_open"] - 1.0))
                if np.isfinite(r_so_far) and r_so_far > 0:
                    pos["extended"] = True
                    due = False
            if due:
                with np.errstate(divide="ignore", invalid="ignore"):
                    r_full = float(np.nanmean(cpx / pos["entry_open"] - 1.0))
                day_r -= _charge(hs, "sell", sw)
                trades.append(
                    {
                        "entry": pos["entry_dt"],
                        "exit": dt,
                        "codes": hs,
                        "ret": round(r_full, 4) if np.isfinite(r_full) else None,
                    }
                )
                slots[s] = None

        eq *= 1.0 + day_r
        curve.append((dt, eq))
        held = any(p is not None for p in slots)
        if held:
            invested_days += 1
            if idle > 0 and idle_start is not None:
                cash_streaks.append({"start": idle_start, "end": dates[i - 1] if i else dt, "n": idle})
            idle = 0
            idle_start = None
        else:
            if idle == 0:
                idle_start = dt
            idle += 1
            if idle > max_cash_streak:
                max_cash_streak = idle
    if idle > 0 and idle_start is not None:
        cash_streaks.append({"start": idle_start, "end": dates[-1], "n": idle})

    warmup_cut = dates[min(59, len(dates) - 1)] if dates else ""
    post = [s for s in cash_streaks if s["start"] > warmup_cut]
    stats = {
        "cost_buy": round(cost_buy, 2),
        "cost_sell": round(cost_sell, 2),
        "n_orders": n_orders,
        "n_min5": n_min5,
        "min5_pct": round(100.0 * n_min5 / n_orders, 1) if n_orders else 0.0,
        "invested_days": invested_days,
        "invested_pct": round(100.0 * invested_days / max(len(dates), 1), 1),
        "max_cash_streak": max_cash_streak,
        "max_cash_streak_post60": max((s["n"] for s in post), default=0),
        "n_cash_streaks_ge10": sum(1 for s in cash_streaks if s["n"] >= 10),
        "n_off_entries": n_off_entries,
        "cash_streaks": cash_streaks,
    }
    return curve, trades, stats


def build_momentum_bt_data(
    daily: pd.DataFrame,
    dates: list[str],
    *,
    min_adv: float,
    listed_days: int,
    ma: int,
    index_code: str = "000300",
) -> dict:
    """从后复权日 K 构造双槽回测所需矩阵与门控。"""
    from quant.data.store import read_index_daily
    from quant.data.universe import _is_st_name

    d = daily.copy()
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
    d["code"] = d["code"].astype(str)
    if "name" not in d.columns:
        d["name"] = ""
    if "amount" not in d.columns:
        d["amount"] = 0.0
    d = d[d["date"].isin(dates)]
    if "name" in d.columns:
        last_name = d.groupby("code")["name"].last()
        st = {str(c) for c, n in last_name.items() if _is_st_name(str(n))}
        d = d[~d["code"].isin(st)]
    d = d[~d["code"].str.startswith(("4", "8", "9"))]
    close = d.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(dates)
    open_ = d.pivot_table(index="date", columns="code", values="open", aggfunc="last").reindex(dates)
    amount = d.pivot_table(index="date", columns="code", values="amount", aggfunc="last").reindex(dates)
    codes = [str(c) for c in close.columns]
    close.columns = open_.columns = amount.columns = codes
    ret_cc = close.pct_change()
    adv = amount.rolling(20, min_periods=10).mean()
    listed = close.notna().astype(float).cumsum()
    th = pd.Series({c: limit_up_th(c) for c in codes})
    lu_open = (open_ / close.shift(1) - 1.0).ge(th * 0.98, axis=1)
    valid = (adv >= min_adv) & close.notna() & open_.notna() & (listed >= listed_days)
    tradable = valid & ~ret_cc.ge(th, axis=1).fillna(False)
    ranks = rank_lists(ret_cc.where(tradable), tradable, dates, codes)
    start, end = dates[0], dates[-1]
    idx = read_index_daily(index_code, start=start, end=end)
    idx["date"] = pd.to_datetime(idx["date"]).dt.strftime("%Y-%m-%d")
    mkt = idx.drop_duplicates("date").set_index("date")["close"].reindex(dates).astype(float)
    min_p = max(15, ma - 20)
    gate = (mkt > mkt.rolling(ma, min_periods=min_p).mean()).shift(1).fillna(False)
    return {
        "close": close,
        "open_": open_,
        "ranks": ranks,
        "lu_open": lu_open,
        "gate": gate,
        "codes": codes,
    }


def curve_metrics(curve: list[tuple[str, float]]) -> dict:
    """权益曲线：累计/年化/波动/Sharpe/回撤/分年。"""
    if len(curve) < 3:
        return {}
    vals = np.array([e[1] for e in curve], dtype=float)
    n = len(vals) - 1
    total = float(vals[-1] / vals[0] - 1.0)
    ann = (1.0 + total) ** (252.0 / max(n, 1)) - 1.0
    rets = np.diff(vals) / np.clip(vals[:-1], 1e-12, None)
    vol = float(rets.std(ddof=1) * np.sqrt(252.0)) if len(rets) > 2 else 0.0
    peak = np.maximum.accumulate(vals)
    mdd = float(((vals - peak) / np.clip(peak, 1e-12, None)).min())
    df = pd.DataFrame(curve, columns=["date", "eq"])
    df["date"] = pd.to_datetime(df["date"])
    by_year: dict[str, float] = {}
    for y, g in df.groupby(df["date"].dt.year):
        if len(g) < 2:
            continue
        by_year[str(int(y))] = round(float(g["eq"].iloc[-1] / g["eq"].iloc[0] - 1.0) * 100, 2)
    pos_years = sum(1 for v in by_year.values() if v > 0)
    worst_year = min(by_year.values()) if by_year else 0.0
    return {
        "ret": round(total * 100, 2),
        "ann": round(ann * 100, 2),
        "vol": round(vol * 100, 2),
        "sharpe": round(ann / vol, 3) if vol > 1e-12 else 0.0,
        "mdd": round(mdd * 100, 2),
        "by_year": by_year,
        "pos_years": int(pos_years),
        "n_years": int(len(by_year)),
        "worst_year": round(float(worst_year), 2),
    }
