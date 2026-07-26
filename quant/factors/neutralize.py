"""截面中性化：行业哑变量 + log 市值回归残差，再做截面 z-score。"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

import numpy as np

from quant.factors.base import FactorRow


def _zscore(vals: np.ndarray) -> np.ndarray:
    if len(vals) < 2:
        return np.zeros_like(vals, dtype=float)
    mu = float(np.nanmean(vals))
    sd = float(np.nanstd(vals, ddof=1))
    if sd < 1e-12 or not np.isfinite(sd):
        return np.zeros_like(vals, dtype=float)
    return (vals - mu) / sd


def winsorize(vals: np.ndarray, lo: float = 0.01, hi: float = 0.99) -> np.ndarray:
    """按分位数缩尾；NaN 保持 NaN。"""
    out = vals.copy()
    mask = np.isfinite(out)
    if int(mask.sum()) < 5:
        return out
    ql, qh = np.nanquantile(out[mask], [lo, hi])
    out[mask] = np.clip(out[mask], ql, qh)
    return out


def neutralize_cross_section(
    raw_values: list[float | None],
    *,
    industries: list[str],
    log_mcaps: list[float | None],
    min_names: int = 5,
    winsor: bool = True,
) -> list[float | None]:
    """单因子截面：winsorize → industry + log_mcap 回归 → 残差 z-score。

    缺行业时仍做市值中性；缺市值时仅行业；两者都缺则只做截面 z-score。
    """
    n = len(raw_values)
    if n < min_names:
        return [None] * n

    y = np.array([np.nan if v is None else float(v) for v in raw_values], dtype=float)
    if winsor:
        y = winsorize(y)
    valid = np.isfinite(y)
    if int(valid.sum()) < min_names:
        return [None] * n

    # 设计矩阵
    ind_list = sorted({ind for ind, ok in zip(industries, valid) if ok and ind})
    use_ind = len(ind_list) >= 2
    use_size = sum(
        1 for m, ok in zip(log_mcaps, valid) if ok and m is not None and np.isfinite(m)
    ) >= max(min_names, int(0.5 * valid.sum()))

    cols: list[np.ndarray] = []
    if use_ind:
        for ind in ind_list[:-1]:  # 丢一个避免共线
            cols.append(np.array([1.0 if (ok and industries[i] == ind) else 0.0 for i, ok in enumerate(valid)]))
    if use_size:
        size_col = np.array(
            [
                float(log_mcaps[i]) if (ok and log_mcaps[i] is not None) else np.nan
                for i, ok in enumerate(valid)
            ],
            dtype=float,
        )
        # 缺失市值用有效样本均值填，仅作回归稳定；该行仍参与
        fill = float(np.nanmean(size_col[valid])) if np.any(np.isfinite(size_col[valid])) else 0.0
        size_col = np.where(np.isfinite(size_col), size_col, fill)
        cols.append(size_col)

    idx = np.where(valid)[0]
    y_v = y[idx]
    if not cols:
        z = _zscore(y_v)
        out: list[float | None] = [None] * n
        for j, i in enumerate(idx):
            out[i] = float(z[j])
        return out

    X = np.column_stack([np.ones(n)] + cols)[idx]
    # 最小二乘；奇异则退回 z-score
    try:
        beta, *_ = np.linalg.lstsq(X, y_v, rcond=None)
        resid = y_v - X @ beta
    except np.linalg.LinAlgError:
        resid = y_v - float(np.mean(y_v))

    z = _zscore(resid)
    out = [None] * n
    for j, i in enumerate(idx):
        out[i] = float(z[j])
    return out


def neutralize_panel_rows(
    rows: list[FactorRow],
    *,
    factor_names: Iterable[str] | None = None,
    min_names: int = 5,
) -> list[FactorRow]:
    """就地写入 row.neutral；按同一 date 分组中性化。"""
    by_date: dict[str, list[FactorRow]] = defaultdict(list)
    for r in rows:
        by_date[r.date].append(r)

    names: set[str] = set()
    if factor_names is None:
        for r in rows:
            names.update(r.raw.keys())
    else:
        names = set(factor_names)

    for _date, group in by_date.items():
        industries = [r.industry for r in group]
        log_mcaps = [r.log_mcap for r in group]
        for fname in sorted(names):
            raws = [r.raw.get(fname) for r in group]
            neut = neutralize_cross_section(
                raws,
                industries=industries,
                log_mcaps=log_mcaps,
                min_names=min_names,
            )
            for r, v in zip(group, neut):
                if v is not None:
                    r.neutral[fname] = v
    return rows
