"""回测参数敏感性报告。"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable

import pandas as pd

from quant.research.sensitivity import scan_param

# ProcessPool initializer 上下文（Windows spawn 友好）
_SENS_CTX: dict[str, Any] = {}


def _sens_ctx_init(ctx: dict[str, Any]) -> None:
    _SENS_CTX.clear()
    _SENS_CTX.update(ctx)


def _sens_job(job: tuple[str, float]) -> tuple[str, float, float]:
    """单参数点：返回 (param, value, sharpe)。依赖 ``_sens_ctx_init``。"""
    from quant.backtest.engine import ExitConfig, run_backtest
    from quant.backtest.metrics import compute_metrics
    from quant.portfolio.target import TargetPortfolio

    param, value = job
    ctx = _SENS_CTX
    defaults = dict(ctx["defaults"])
    defaults[param] = value
    ne = int(defaults.get("n_enter", 8))
    n_exit = int(defaults.get("n_exit", max(ne + 5, 15)))
    tv = float(defaults.get("target_vol", 0.15))
    ba = float(defaults.get("buffer_abs", 0.01))
    sc = float(defaults.get("sector_cap", 0.40))
    max_positions = int(ctx["max_positions"])
    daily: pd.DataFrame = ctx["daily"]
    dates: list[str] = ctx["dates"]
    alpha_by_date: dict[str, dict[str, float]] = ctx["alpha_by_date"]
    strict = bool(ctx["strict_signals"])
    use_exit = bool(ctx["use_exit"])

    def alpha_fn(d: str, _rows: dict) -> dict[str, float]:
        return alpha_by_date.get(d, {})

    try:
        pol = TargetPortfolio.from_config(
            n_enter=ne,
            n_exit=n_exit,
            max_stocks=max_positions,
            target_vol=tv,
            buffer_abs=ba,
            sector_cap=sc,
            daily=daily,
        )
        exit_cfg = ExitConfig() if use_exit else None
        broker = run_backtest(
            daily=daily,
            dates=dates,
            alpha_fn=alpha_fn,
            policy=pol,
            max_positions=max_positions,
            exit_config=exit_cfg,
            strict_signals=strict,
        )
        return param, value, float(compute_metrics(broker).get("sharpe") or 0.0)
    except Exception:
        return param, value, float("nan")


def run_backtest_sensitivity(
    *,
    base_run_fn: Callable[[dict[str, float]], float] | None = None,
    grids: dict[str, list[float]] | None = None,
    workers: int = 1,
    parallel_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对关键参数做单变量 Sharpe 扫描。

    - ``workers<=1``：用 ``base_run_fn`` 串行（与旧行为一致）。
    - ``workers>1``：须传 ``parallel_ctx``（含 daily/dates/alpha_by_date 等），
      ProcessPool 按 grid 原序回写结果。
    """
    grids = grids or {
        "n_enter": [6.0, 8.0, 10.0, 12.0],
        "target_vol": [0.12, 0.15, 0.18],
        "buffer_abs": [0.005, 0.01, 0.02],
        "sector_cap": [0.30, 0.40, 0.50],
    }
    workers = max(1, int(workers))
    out: dict[str, Any] = {}

    if workers <= 1:
        if base_run_fn is None:
            raise ValueError("workers<=1 时须提供 base_run_fn")
        for param, grid in grids.items():
            res = scan_param(
                param, grid, run_fn=lambda v, p=param: base_run_fn({p: v})
            )
            out[param] = {
                "grid": res.grid,
                "sharpes": res.sharpes,
                "stability": res.stability,
                "peak_to_median": res.peak_to_median,
            }
        return out

    if parallel_ctx is None:
        raise ValueError("workers>1 时须提供 parallel_ctx（勿传不可 pickle 的闭包）")

    from quant.research.sensitivity import SensitivityResult, _stability

    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_sens_ctx_init,
        initargs=(parallel_ctx,),
    ) as pool:
        for param, grid in grids.items():
            jobs = [(param, float(v)) for v in grid]
            # map 保序，与 grid 对齐
            results = list(pool.map(_sens_job, jobs))
            sharpes = [s for _, _, s in results]
            stab, ptm = _stability(sharpes)
            res = SensitivityResult(
                param=param,
                grid=list(grid),
                sharpes=sharpes,
                stability=round(stab, 3),
                peak_to_median=round(ptm, 3),
            )
            out[param] = {
                "grid": res.grid,
                "sharpes": res.sharpes,
                "stability": res.stability,
                "peak_to_median": res.peak_to_median,
            }
    return out
