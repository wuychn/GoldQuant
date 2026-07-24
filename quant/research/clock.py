"""模拟时钟：回测/纸交易与墙钟解耦。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from quant.timeutil import CN_TZ, cn_now


@dataclass
class SimulationClock:
    """当前模拟时刻；三确认、14:30 判定均以此为准。"""

    now: datetime = field(default_factory=cn_now)

    def set(self, dt: datetime) -> None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        self.now = dt

    @property
    def date_str(self) -> str:
        return self.now.strftime("%Y-%m-%d")

    @property
    def time_str(self) -> str:
        return self.now.strftime("%H:%M:%S")

    def is_late_session(self, after: str = "14:30") -> bool:
        hh, mm = (int(x) for x in after.split(":"))
        return self.now.hour > hh or (self.now.hour == hh and self.now.minute >= mm)
