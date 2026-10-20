"""Scheduler 配置：仅读 quant.yml scheduler 段。"""

from __future__ import annotations

from typing import Any

from quant.config import load_quant_config


def load_scheduler_config() -> dict[str, Any]:
    cfg = load_quant_config()
    sched = cfg.get("scheduler") or {}
    return {
        "enabled": bool(sched.get("enabled", True)),
        "timezone": str(sched.get("timezone") or "Asia/Shanghai"),
        "update_daily_time": str(sched.get("update_daily_time") or "16:00"),
        "maintain_weekly_day": str(sched.get("maintain_weekly_day") or "fri"),
        "maintain_weekly_time": str(sched.get("maintain_weekly_time") or "22:00"),
        "daily_decision_time": str(sched.get("daily_decision_time") or "20:10"),
        "misfire_grace_sec": int(sched.get("misfire_grace_sec", 600)),
    }
