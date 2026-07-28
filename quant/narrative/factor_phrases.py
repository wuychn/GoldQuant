"""因子贡献 → 中文归因短语（推送文案用）。

根据 ``compose.alpha_attribution`` 返回的 (factor, contribution) 生成"动量强/
资金流入/接近新高"等自然语言，替代 r1 评分体系的战法文案。
"""

from __future__ import annotations

# 因子名 → (强/正贡献文案, 弱/负贡献文案)
_FACTOR_PHRASES: dict[str, tuple[str, str]] = {
    "mom_60": ("中期动量强", "动量偏弱"),
    "mom_120_20": ("中长期动量强", "动量偏弱"),
    "mom_20": ("短期反转到位", "短期超买"),
    "mom_accel": ("趋势在加速", "动能衰竭"),
    "dist_high_252": ("接近一年新高", "距高点较远"),
    "ma_spread": ("均线多头发散", "均线缠绕"),
    "spread_accel_5": ("发散在加速", "发散收敛"),
    "ma_slope_20": ("均线向上", "均线走平"),
    "vol_ratio_5_20": ("量能放大", "量能萎缩"),
    "turnover_z_60": ("换手活跃", "换手低迷"),
    "vol_price_corr_20": ("量价配合", "量价背离"),
    "flow_ratio_5": ("主力资金流入", "资金流出"),
    "theme_mom": ("板块动量领先", "板块走弱"),
    "hot_rank_z": ("人气居前", "人气低迷"),
    "eff_ratio_60": ("趋势顺滑", "走势反复"),
    "flip_rate_60": ("方向稳定", "上蹿下跳"),
    "vol_60": ("低波稳健", "波动偏大"),
    "downside_vol_60": ("下行风险低", "下行波动大"),
}


def factor_phrase(factor: str, contribution: float) -> str:
    """根据因子贡献符号返回强/弱短语；未登记因子回退到因子名。"""
    strong, weak = _FACTOR_PHRASES.get(factor, (factor, factor))
    return strong if contribution >= 0 else weak


def attribution_summary(attribution: list[tuple[str, float]], *, max_k: int = 2) -> str:
    """top-k 因子贡献 → 顿号连接的中文短语串（如「中期动量强、主力资金流入」）。"""
    parts = [factor_phrase(f, c) for f, c in attribution[:max_k] if abs(c) > 1e-9]
    return "、".join(parts)
