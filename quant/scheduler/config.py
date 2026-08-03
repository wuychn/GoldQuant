"""Scheduler 配置：仅读 quant.yml scheduler 段。"""

from __future__ import annotations

from typing import Any

from quant.config import load_quant_config
from quant.scheduler.times import DEFAULT_DURING_MARKET_TIMES


def load_scheduler_config() -> dict[str, Any]:
    cfg = load_quant_config()
    sched = cfg.get("scheduler") or {}
    during = str(sched.get("during_market_times") or "").strip()
    if not during:
        during = DEFAULT_DURING_MARKET_TIMES
    return {
        "enabled": bool(sched.get("enabled", True)),
        "timezone": str(sched.get("timezone") or "Asia/Shanghai"),
        "news_hours": str(sched.get("news_hours") or "8,9,10,11,12,13,14,15,16,17,18,19,20,21,22"),
        "news_minute": int(sched.get("news_minute", 0)),
        "pre_market_time": str(sched.get("pre_market_time") or "09:25"),
        "during_market_times": during,
        "post_market_lunch_time": str(sched.get("post_market_lunch_time") or "11:50"),
        "update_daily_time": str(sched.get("update_daily_time") or "18:00"),
        "maintain_weekly_day": str(sched.get("maintain_weekly_day") or "fri"),
        "maintain_weekly_time": str(sched.get("maintain_weekly_time") or "22:00"),
        "daily_decision_time": str(sched.get("daily_decision_time") or "20:10"),
        "prefetch_concepts_enabled": bool(sched.get("prefetch_concepts_enabled", True)),
        "prefetch_concepts_time": str(sched.get("prefetch_concepts_time") or "05:00"),
        "misfire_grace_sec": int(sched.get("misfire_grace_sec", 600)),
        "in_process_modes": list(
            sched.get("in_process_modes")
            or [
                "news",
                "pre_market",
                "during_market",
                "post_market_lunch",
                "post_market_evening",
            ]
        ),
    }
