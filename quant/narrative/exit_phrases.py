"""出场原因 → 中文文案（推送文案用）。

把 ``exit/rules`` 的 ExitSignal.reason 与决策卡 action reason 映射成自然语言，
替代 r1 评分体系的卖点文案。
"""

from __future__ import annotations

_EXIT_LABELS: dict[str, str] = {
    "hard_stop": "硬止损",
    "atr_trailing": "ATR跟踪止损",
    "trend_stop_ma20": "破MA20趋势止损",
    "trend_stop": "趋势止损",
    "time_stop": "时间止损",
    "near_atr_trailing": "临近止损",
    "out_of_target": "掉出目标组合",
    "rebalance": "组合再平衡",
    "within_buffer": "缓冲带内",
    "new_position": "新建仓位",
    "exit_signal": "出场信号",
}


def exit_reason_label(reason: str | None) -> str:
    """reason code → 中文；未登记回退原值，空值返回空串。"""
    if not reason:
        return ""
    return _EXIT_LABELS.get(str(reason).strip(), str(reason).strip())
