"""调度时点生成（默认盘中每 7 分钟）。"""

from __future__ import annotations

from datetime import datetime, timedelta


def build_during_market_schedule(
    *,
    interval_minutes: int = 7,
    morning: tuple[int, int, int, int] = (9, 37, 11, 30),
    afternoon: tuple[int, int, int, int] = (13, 0, 15, 0),
) -> str:
    def _session_times(start_h: int, start_m: int, end_h: int, end_m: int) -> list[str]:
        start = datetime(2000, 1, 1, start_h, start_m)
        end = datetime(2000, 1, 1, end_h, end_m)
        step = timedelta(minutes=interval_minutes)
        t = start
        out: list[str] = []
        while t <= end:
            out.append(t.strftime("%H:%M"))
            t += step
        return out

    times = _session_times(*morning) + _session_times(*afternoon)
    return ",".join(times)


DEFAULT_DURING_MARKET_TIMES = build_during_market_schedule()
