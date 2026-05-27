"""量化流水线定时任务：在服务启动后按配置触发 `python -m quant <mode>`。

交易日历与 `quant.orchestrator.pipeline_allowed_for_mode` 一致（`common_util.is_real_workday_cn`）。
子进程调用 CLI，避免 fetch 失败时 `sys.exit` 拖垮 Web  worker。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.utils.common_util import is_real_workday_cn

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _parse_int_hours_csv(s: str) -> list[int]:
    raw = [p.strip() for p in str(s).split(",") if p.strip()]
    if not raw:
        raise ValueError("新闻小时列表为空")
    out: list[int] = []
    for x in raw:
        h = int(x)
        if not (0 <= h <= 23):
            raise ValueError(f"无效小时: {h}（须 0–23）")
        out.append(h)
    return out


def _parse_hh_mm(s: str) -> tuple[int, int]:
    t = str(s).strip()
    if not t:
        raise ValueError("时间为空")
    if ":" in t:
        a, b = t.split(":", 1)
        return int(a), int(b)
    # 形如 0925 → 不推荐，支持纯小时 "9" → 9:00
    if len(t) <= 2:
        return int(t), 0
    if len(t) == 4 and t.isdigit():
        return int(t[:2]), int(t[2:])
    raise ValueError(f"无法解析时间: {t!r}（期望 HH:MM）")


def _parse_time_list_csv(s: str) -> list[tuple[int, int]]:
    parts = [p.strip() for p in str(s).split(",") if p.strip()]
    if not parts:
        raise ValueError("盘中时点列表为空")
    return [_parse_hh_mm(p) for p in parts]


def _evening_calendar_hit() -> bool:
    """与 orchestrator `post_market_evening` 一致：今日或次日为工作日即触发窗口内可跑。"""
    d = datetime.now().date()
    return is_real_workday_cn(d) or is_real_workday_cn(d + timedelta(days=1))


def _invoke_quant_cli(mode: str, *, timeout_sec: float | None) -> None:
    cmd = [sys.executable, "-m", "quant", mode]
    logger.info("[quant-scheduler] 执行: cwd=%s %s", _PROJECT_ROOT, " ".join(cmd))
    kw: dict = {
        "cwd": str(_PROJECT_ROOT),
        "env": os.environ.copy(),
        "capture_output": True,
        "text": True,
    }
    if timeout_sec is not None and timeout_sec > 0:
        kw["timeout"] = timeout_sec
    try:
        proc = subprocess.run(cmd, **kw)
    except subprocess.TimeoutExpired:
        logger.error("[quant-scheduler] 超时 mode=%s timeout=%s", mode, timeout_sec)
        return
    except Exception:
        logger.exception("[quant-scheduler] 子进程启动失败 mode=%s", mode)
        return
    if proc.returncode != 0:
        tail_out = (proc.stdout or "")[-2000:]
        tail_err = (proc.stderr or "")[-2000:]
        logger.error(
            "[quant-scheduler] 退出码 %s mode=%s\nstdout<<<\n%s\n>>> stderr<<<\n%s\n>>>",
            proc.returncode,
            mode,
            tail_out,
            tail_err,
        )
    else:
        logger.info("[quant-scheduler] 完成 mode=%s", mode)


def _job_news(settings: Settings) -> None:
    _invoke_quant_cli("news", timeout_sec=settings.QUANT_SCHED_SUBPROCESS_TIMEOUT_SEC)


def _job_pre_market(settings: Settings) -> None:
    if not is_real_workday_cn():
        return
    _invoke_quant_cli("pre_market", timeout_sec=settings.QUANT_SCHED_SUBPROCESS_TIMEOUT_SEC)


def _job_during_market(settings: Settings) -> None:
    if not is_real_workday_cn():
        return
    _invoke_quant_cli("during_market", timeout_sec=settings.QUANT_SCHED_SUBPROCESS_TIMEOUT_SEC)


def _job_post_market_lunch(settings: Settings) -> None:
    if not is_real_workday_cn():
        return
    _invoke_quant_cli(
        "post_market_lunch",
        timeout_sec=settings.QUANT_SCHED_SUBPROCESS_TIMEOUT_SEC,
    )


def _job_post_market_evening(settings: Settings) -> None:
    if not _evening_calendar_hit():
        return
    _invoke_quant_cli(
        "post_market_evening",
        timeout_sec=settings.QUANT_SCHED_SUBPROCESS_TIMEOUT_SEC,
    )


def build_quant_scheduler(settings: Settings) -> BackgroundScheduler | None:
    """按 `Settings` 构建并注册任务；调用方需在 lifespan 内 `start()` / `shutdown()`。"""
    if not settings.QUANT_SCHEDULER_ENABLED:
        logger.info("[quant-scheduler] 已通过配置禁用 (QUANT_SCHEDULER_ENABLED=false)")
        return None
    try:
        tz = ZoneInfo(settings.QUANT_SCHED_TIMEZONE)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"无效的 IANA 时区: {settings.QUANT_SCHED_TIMEZONE}") from e

    sched = BackgroundScheduler(timezone=tz)
    defaults = dict(
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(120, settings.QUANT_SCHED_MISFIRE_GRACE_SEC),
    )

    hours = _parse_int_hours_csv(settings.QUANT_SCHED_NEWS_HOURS)
    hour_spec = ",".join(str(h) for h in hours)
    sched.add_job(
        _job_news,
        CronTrigger(
            timezone=tz,
            hour=hour_spec,
            minute=settings.QUANT_SCHED_NEWS_MINUTE,
        ),
        args=[settings],
        id="quant_news",
        **defaults,
    )

    ph, pm = _parse_hh_mm(settings.QUANT_SCHED_PRE_MARKET_TIME)
    sched.add_job(
        _job_pre_market,
        CronTrigger(timezone=tz, hour=ph, minute=pm),
        args=[settings],
        id="quant_pre_market",
        **defaults,
    )

    during_times = _parse_time_list_csv(settings.QUANT_SCHED_DURING_MARKET_TIMES)
    for i, (h, m) in enumerate(during_times):
        sched.add_job(
            _job_during_market,
            CronTrigger(timezone=tz, hour=h, minute=m),
            args=[settings],
            id=f"quant_during_{i:03d}_{h:02d}{m:02d}",
            **defaults,
        )

    lh, lm = _parse_hh_mm(settings.QUANT_SCHED_POST_MARKET_LUNCH_TIME)
    sched.add_job(
        _job_post_market_lunch,
        CronTrigger(timezone=tz, hour=lh, minute=lm),
        args=[settings],
        id="quant_post_market_lunch",
        **defaults,
    )

    eh, em = _parse_hh_mm(settings.QUANT_SCHED_POST_MARKET_EVENING_TIME)
    sched.add_job(
        _job_post_market_evening,
        CronTrigger(timezone=tz, hour=eh, minute=em),
        args=[settings],
        id="quant_post_market_evening",
        **defaults,
    )

    n_during = len(during_times)
    logger.info(
        "[quant-scheduler] 已注册: news(hours=%s @ :%02d), pre=%02d:%02d, during×%d, "
        "lunch=%02d:%02d, evening=%02d:%02d, tz=%s",
        hour_spec,
        settings.QUANT_SCHED_NEWS_MINUTE,
        ph,
        pm,
        n_during,
        lh,
        lm,
        eh,
        em,
        settings.QUANT_SCHED_TIMEZONE,
    )
    return sched


def shutdown_quant_scheduler(sched: BackgroundScheduler) -> None:
    if sched.running:
        sched.shutdown(wait=False)
    logger.info("[quant-scheduler] 已停止")
