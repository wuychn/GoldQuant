"""因子级 IC 分析（基于 FactorRow 面板）。

- 日度截面 RankIC → IC 均值 / ICIR / t 值
- IC 衰减曲线（1/3/5/10/20 日前瞻）
- 五分位多头-空头价差（rank quintile spread）
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from quant.factors.base import FactorRow
from quant.factors.neutralize import neutralize_cross_section


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return 0.0
    try:
        from scipy.stats import spearmanr

        c, _ = spearmanr(a, b)
        return float(c) if c == c else 0.0
    except Exception:
        a = np.argsort(np.argsort(a)).astype(float)
        b = np.argsort(np.argsort(b)).astype(float)
        if a.std() < 1e-12 or b.std() < 1e-12:
            return 0.0
        return float(np.corrcoef(a, b)[0, 1])


def _forward_at(row: FactorRow, horizon: int) -> float | None:
    if horizon == 5 and row.forward_return_pct is not None:
        return float(row.forward_return_pct)
    fwd = row.meta.get("fwd") or {}
    v = fwd.get(horizon)
    return float(v) if v is not None else None


def daily_rank_ic(
    rows: list[FactorRow],
    factor_name: str,
    *,
    min_names: int = 5,
    neutralize_return: bool = True,
    horizon: int = 5,
) -> dict[str, Any]:
    """单因子日度截面 RankIC 汇总（用 neutral z；缺则回退 raw）。

    ``neutralize_return=True`` 时对每日截面 forward_return 做行业+log市值中性化后再算 IC
    （与因子中性化同口径），剔除市场/行业/市值 beta，度量纯 alpha IC。旧版用原始
    forward_return，IC 系统性混入风格溢价被高估（P0-⑦）。
    """
    by_date: dict[str, list[FactorRow]] = defaultdict(list)
    for r in rows:
        by_date[r.date].append(r)

    ics: list[float] = []
    for d in sorted(by_date):
        grp = by_date[d]
        # 中性化前瞻收益（与因子同口径）→ 纯 alpha IC 标签
        raw_fwd = [_forward_at(r, horizon) for r in grp]
        if neutralize_return:
            fwd_neut = neutralize_cross_section(
                raw_fwd,
                industries=[r.industry for r in grp],
                log_mcaps=[r.log_mcap for r in grp],
                min_names=min_names,
            )
        else:
            fwd_neut = raw_fwd
        scores = []
        rets = []
        for r, fn in zip(grp, fwd_neut):
            v = r.neutral.get(factor_name) if r.neutral else None
            if v is None:
                v = r.raw.get(factor_name)
            if v is None or fn is None:
                continue
            scores.append(float(v))
            rets.append(float(fn))
        if len(scores) < min_names:
            continue
        ics.append(_spearman(np.array(scores), np.array(rets)))

    if not ics:
        return {"factor": factor_name, "ic_mean": 0.0, "ic_std": 0.0, "icir": 0.0, "t_stat": 0.0, "n_days": 0}
    arr = np.array(ics, dtype=float)
    mu = float(arr.mean())
    sd = float(arr.std(ddof=1)) if len(arr) > 1 else 0.0
    icir = mu / sd if sd > 1e-12 else 0.0
    t = mu / (sd / np.sqrt(len(arr))) if sd > 1e-12 else 0.0
    return {
        "factor": factor_name,
        "ic_mean": round(mu, 4),
        "ic_std": round(sd, 4),
        "icir": round(icir, 4),
        "t_stat": round(t, 2),
        "n_days": len(ics),
    }


def ic_decay(rows: list[FactorRow], factor_name: str, horizons=(1, 3, 5, 10, 20)) -> dict[int, float]:
    """IC 衰减曲线。需要 rows 上挂多档前瞻收益（meta['fwd']）。

    多档前瞻由 panel_builder 写入 ``meta['fwd']``；主 IC 默认 5 日。
    """
    fwd_map: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        v = r.neutral.get(factor_name) if r.neutral else None
        if v is None:
            v = r.raw.get(factor_name)
        if v is None:
            continue
        fwd = r.meta.get("fwd") or {}
        for h in horizons:
            if h in fwd and fwd[h] is not None:
                fwd_map[h].append((float(v), float(fwd[h])))
    out: dict[int, float] = {}
    for h in horizons:
        if len(fwd_map[h]) < 10:
            out[h] = 0.0
            continue
        a = np.array([x[0] for x in fwd_map[h]])
        b = np.array([x[1] for x in fwd_map[h]])
        out[h] = round(_spearman(a, b), 4)
    return out


def quintile_spread(rows: list[FactorRow], factor_name: str) -> dict[str, Any]:
    """五分位多头-空头价差（用截面 z 分组）。"""
    by_date: dict[str, list[FactorRow]] = defaultdict(list)
    for r in rows:
        by_date[r.date].append(r)

    ls_rets: list[float] = []
    for d in sorted(by_date):
        # 排除因子值缺失的样本，避免 -inf 兜底污染空头组
        def _val(r):
            v = r.neutral.get(factor_name) if r.neutral else None
            if v is None:
                v = r.raw.get(factor_name)
            return float(v) if v is not None and np.isfinite(v) else None

        grp = [r for r in by_date[d] if r.forward_return_pct is not None and _val(r) is not None]
        if len(grp) < 10:
            continue
        grp.sort(key=lambda r: _val(r))
        n = len(grp)
        q = max(1, n // 5)
        top = grp[-q:]
        bot = grp[:q]
        ls_rets.append(float(np.mean([r.forward_return_pct for r in top])) - float(np.mean([r.forward_return_pct for r in bot])))

    if not ls_rets:
        return {"factor": factor_name, "ls_spread_bps": 0.0, "n_days": 0, "hit_rate": 0.0}
    arr = np.array(ls_rets)
    return {
        "factor": factor_name,
        "ls_spread_bps": round(float(arr.mean()) * 100, 2),  # % → bps
        "n_days": len(arr),
        "hit_rate": round(float((arr > 0).mean()), 4),
    }


def factor_ic_report(
    rows: list[FactorRow],
    factor_names: list[str],
    *,
    horizon: int = 5,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> list[dict[str, Any]]:
    """单因子 IC 报告。``horizons`` 用于多持有期 ICIR 混合（权重拟合用）。"""
    out = []
    for f in factor_names:
        ic = daily_rank_ic(rows, f, horizon=horizon)
        qs = quintile_spread(rows, f)
        decay = ic_decay(rows, f)
        blend_icirs: list[float] = []
        for h in horizons:
            sub = daily_rank_ic(rows, f, horizon=h)
            if sub.get("icir") is not None:
                blend_icirs.append(float(sub["icir"]))
        blended_icir = float(np.mean(blend_icirs)) if blend_icirs else float(ic.get("icir") or 0.0)
        out.append({
            **ic,
            "icir": round(blended_icir, 4),
            "primary_horizon": horizon,
            "ls_spread_bps": qs["ls_spread_bps"],
            "hit_rate": qs["hit_rate"],
            "ic_decay": decay,
        })
    return out
