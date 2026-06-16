"""盘口异动 enrich 后筛选：月线涨幅 + 本月阳线 + 多周期主升。"""

from __future__ import annotations

from typing import Any

from quant.pool.candidate_config import load_candidate_config, pkyd_three_month_min_pct
from quant.scoring.tech_indicators import (
    current_month_is_yang,
    monthly_three_month_return_pct,
    multi_timeframe_main_wave_up,
)


def passes_pkyd_main_wave_filter(
    stock: dict,
    *,
    mw_cfg: dict[str, Any] | None = None,
    candidate_cfg: dict | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """盘口异动 post-filter（enrich 后）：不要求双标签、不先按标签截断。

    条件（全部满足）：
    - 月线近 3 个月累计涨幅 > 配置阈值（默认 30%）
    - 本月阳线
    - 日/周/月线均线发散向上（主升浪）
    """
    from quant.config import load_gates_config

    hist = stock.get("历史行情") or []
    c_cfg = candidate_cfg or load_candidate_config()
    mw = mw_cfg if mw_cfg is not None else (load_gates_config().get("main_wave") or {})
    min_3m = pkyd_three_month_min_pct(c_cfg)
    min_spread = float(mw.get("min_ma_spread_pct", 0.8))

    detail: dict[str, Any] = {}
    ret_3m = monthly_three_month_return_pct(hist)
    detail["近3月涨幅"] = round(ret_3m, 2) if ret_3m is not None else None
    if ret_3m is None:
        return False, f"月线数据不足，无法计算近3月涨幅", detail
    if ret_3m <= min_3m:
        return False, f"近3月涨幅{ret_3m:.1f}%≤{min_3m}%", detail

    yang = current_month_is_yang(hist)
    detail["本月阳线"] = yang
    if not yang:
        return False, "本月非阳线", detail

    mtf_ok, mtf_flags = multi_timeframe_main_wave_up(hist, min_spread_pct=min_spread)
    detail.update(mtf_flags)
    if not mtf_ok:
        missing = [k for k, v in mtf_flags.items() if not v]
        return False, f"多周期主升未满足：{'、'.join(missing)}", detail

    return True, "盘口异动主升筛选通过", detail
