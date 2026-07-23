"""评分/主题工具函数。"""

from __future__ import annotations


def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))
