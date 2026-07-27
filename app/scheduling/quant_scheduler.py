"""量化流水线定时任务：触发 r3 CLI（ops + 日决策）。

子进程调用 ``python -m quant <mode>``，避免拖垮 Web worker。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.utils.common_util import is_real_workday_cn
from app.utils.error_log import log_caught_error, color_red

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


def _run_cmd(cmd: list[str], label: str) -> None:
    logger.info("[quant-scheduler] 执行: cwd=%s %s", _PROJECT_ROOT, " ".join(cmd))
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        log_caught_error(logger, f"[quant-scheduler] 子进程启动失败 {label}", e)
        return
    if proc.returncode != 0:
        err_tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        snippet = " | ".join(line.strip() for line in err_tail[-3:] if line.strip()) or "(无输出)"
        logger.error(
            color_red(f"[子进程异常退出] {label} 退出码={proc.returncode} — {snippet}")
        )
    else:
        logger.info("[quant-scheduler] 完成 %s", label)


def _invoke_quant_cli(mode: str, *extra: str) -> None:
    _run_cmd([sys.executable, "-m", "quant", mode, *extra], f"quant {mode}")


def _invoke_python_module(module: str) -> None:
    """跑 ``python -m <module>``（如 scripts.data.update_daily）。"""
    _run_cmd([sys.executable, "-m", module], module)


def _job_news(_settings: Settings) -> None:
    _invoke_quant_cli("news")


def _job_pre_market(_settings: Settings) -> None:
    if not is_real_workday_cn():
        return
    _invoke_quant_cli("pre_market")


def _job_during_market(_settings: Settings) -> None:
    if not is_real_workday_cn():
        return
    _invoke_quant_cli("during_market")


def _job_post_market_lunch(_settings: Settings) -> None:
    if not is_real_workday_cn():
        return
    _invoke_quant_cli("post_market_lunch")


def _job_post_market_evening(_settings: Settings) -> None:
    if not is_real_workday_cn():
        return
    _invoke_quant_cli("post_market_evening")


def _job_daily_decision(_settings: Settings) -> None:
    """收盘后日决策 + 纸面撮合 + 推送。"""
    if not is_real_workday_cn():
        return
    _invoke_quant_cli("daily_decision")


def _job_maintain_daily(_settings: Settings) -> None:
    """收盘后数据维护（无库建库 / 查漏补漏 / 当日增量），仅交易日。

    见 ``scripts/data/maintain.py``；须早于 ``daily_decision``，日决策依赖当日数据。
    """
    if not is_real_workday_cn():
        return
    _invoke_python_module("scripts.data.maintain")


def _job_prefetch_stock_concepts(_settings: Settings) -> None:
    if not _settings.QUANT_SCHED_PREFETCH_CONCEPTS_ENABLED:
        return
    _invoke_quant_cli("prefetch_concepts")


def build_quant_scheduler(settings: Settings) -> BackgroundScheduler | None:
    if not settings.QUANT_SCHEDULER_ENABLED:
        logger.debug("[quant-scheduler] 已禁用")
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
        CronTrigger(timezone=tz, hour=hour_spec, minute=settings.QUANT_SCHED_NEWS_MINUTE),
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

    # 收盘后数据维护（默认 16:00）：无库建库 / 查漏补漏 / 当日增量；须早于日决策（20:45）
    uph, upm = _parse_hh_mm(settings.QUANT_SCHED_MAINTAIN_DAILY_TIME)
    sched.add_job(
        _job_maintain_daily,
        CronTrigger(timezone=tz, hour=uph, minute=upm),
        args=[settings],
        id="quant_maintain_daily",
        **defaults,
    )

    # 收盘复盘后跑日决策（默认晚间任务后 30 分钟；可用配置覆盖则仍用 evening+0）
    # 固定：evening 时间 + 35 分钟
    from datetime import datetime, timedelta

    base = datetime(2000, 1, 1, eh, em) + timedelta(minutes=35)
    sched.add_job(
        _job_daily_decision,
        CronTrigger(timezone=tz, hour=base.hour, minute=base.minute),
        args=[settings],
        id="quant_daily_decision",
        **defaults,
    )

    if settings.QUANT_SCHED_PREFETCH_CONCEPTS_ENABLED:
        ch, cm = _parse_hh_mm(settings.QUANT_SCHED_PREFETCH_CONCEPTS_TIME)
        sched.add_job(
            _job_prefetch_stock_concepts,
            CronTrigger(timezone=tz, hour=ch, minute=cm),
            args=[settings],
            id="quant_prefetch_stock_concepts",
            **defaults,
        )

    # 旧 weekly ML / 旧 backtest 已退役（改用 scripts/research + scripts/backtest）
    logger.info(
        "[quant-scheduler] r3 已注册: news / pre / during×%d / lunch / evening / maintain_daily / daily_decision",
        len(during_times),
    )
    return sched


def shutdown_quant_scheduler(sched: BackgroundScheduler) -> None:
    if sched.running:
        sched.shutdown(wait=False)
    logger.info("[quant-scheduler] 已停止")
