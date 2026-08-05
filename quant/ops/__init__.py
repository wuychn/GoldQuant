"""r3 日运维：新闻 / 盘前 / 盯盘 / 复盘 + 飞书推送。"""

from __future__ import annotations


def __getattr__(name: str):
    # 惰性重导出：不在包导入时拉入 runner（runner→market_jobs→payload→ops 会循环导入）
    if name == "run_mode":
        from quant.ops.runner import run_mode

        return run_mode
    raise AttributeError(f"module 'quant.ops' has no attribute {name!r}")


__all__ = ["run_mode"]
