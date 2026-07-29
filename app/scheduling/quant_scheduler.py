"""量化流水线定时任务：轻推送 in-process，日决策/maintain 仍子进程。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from common.utils.common_util import is_real_workday_cn
from common.utils.error_log import log_caught_error, color_red
from quant.scheduler.config import load_scheduler_config

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


def _invoke_market_in_process(mode: str) -> None:
    try:
        from quant.jobs.market_jobs import run_market_job

        run_market_job(mode, push=True)
    except Exception as e:
        log_caught_error(logger, f"[quant-scheduler] in-process {mode}", e)


def _run_market_mode(mode: str) -> None:
    sched_cfg = load_scheduler_config()
    if mode in sched_cfg.get("in_process_modes", []):
        _invoke_market_in_process(mode)
    else:
        _invoke_quant_cli(mode)


def _invoke_python_module(module: str) -> None:
    """跑 ``python -m <module>``（如 scripts.data.update_daily）。"""
    _run_cmd([sys.executable, "-m", module], module)


def _job_news() -> None:
    _run_market_mode("news")


def _job_pre_market() -> None:
    if not is_real_workday_cn():
        return
    _run_market_mode("pre_market")


def _job_during_market() -> None:
    if not is_real_workday_cn():
        return
    _run_market_mode("during_market")


def _job_post_market_lunch() -> None:
    if not is_real_workday_cn():
        return
    _run_market_mode("post_market_lunch")


def _job_daily_decision() -> None:
    """晚间选股 + 制定明日计划（作战池/卖出监控），不撮合；买卖在 T+1 盘中。"""
    if not is_real_workday_cn():
        return
    _invoke_quant_cli("daily_decision")


def _job_maintain_daily() -> None:
    """收盘后数据维护（无库建库 / 查漏补漏 / 当日增量），仅交易日。

    见 ``scripts/data/maintain.py``；须早于 ``daily_decision``，日决策依赖当日数据。
    """
    if not is_real_workday_cn():
        return
    _invoke_python_module("scripts.data.maintain")


def _job_prefetch_stock_concepts(sched_cfg: dict) -> None:
    if not sched_cfg.get("prefetch_concepts_enabled"):
        return
    _invoke_quant_cli("prefetch_concepts")


def build_quant_scheduler() -> BackgroundScheduler | None:
    sched_cfg = load_scheduler_config()
    if not sched_cfg.get("enabled", True):
        logger.debug("[quant-scheduler] 已禁用")
        return None
    tz_name = str(sched_cfg.get("timezone") or "Asia/Shanghai")
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"无效的 IANA 时区: {tz_name}") from e

    sched = BackgroundScheduler(timezone=tz)
    defaults = dict(
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(120, int(sched_cfg.get("misfire_grace_sec", 600))),
    )

    hours = _parse_int_hours_csv(str(sched_cfg.get("news_hours") or ""))
    hour_spec = ",".join(str(h) for h in hours)
    sched.add_job(
        _job_news,
        CronTrigger(timezone=tz, hour=hour_spec, minute=int(sched_cfg.get("news_minute", 0))),
        id="quant_news",
        **defaults,
    )

    ph, pm = _parse_hh_mm(str(sched_cfg.get("pre_market_time") or "09:25"))
    sched.add_job(
        _job_pre_market,
        CronTrigger(timezone=tz, hour=ph, minute=pm),
        id="quant_pre_market",
        **defaults,
    )

    during_times = _parse_time_list_csv(str(sched_cfg.get("during_market_times") or ""))
    for i, (h, m) in enumerate(during_times):
        sched.add_job(
            _job_during_market,
            CronTrigger(timezone=tz, hour=h, minute=m),
            id=f"quant_during_{i:03d}_{h:02d}{m:02d}",
            **defaults,
        )

    lh, lm = _parse_hh_mm(str(sched_cfg.get("post_market_lunch_time") or "11:50"))
    sched.add_job(
        _job_post_market_lunch,
        CronTrigger(timezone=tz, hour=lh, minute=lm),
        id="quant_post_market_lunch",
        **defaults,
    )

    uph, upm = _parse_hh_mm(str(sched_cfg.get("maintain_daily_time") or "16:00"))
    sched.add_job(
        _job_maintain_daily,
        CronTrigger(timezone=tz, hour=uph, minute=upm),
        id="quant_maintain_daily",
        **defaults,
    )

    dh, dm = _parse_hh_mm(str(sched_cfg.get("daily_decision_time") or "20:10"))
    sched.add_job(
        _job_daily_decision,
        CronTrigger(timezone=tz, hour=dh, minute=dm),
        id="quant_daily_decision",
        **defaults,
    )

    if sched_cfg.get("prefetch_concepts_enabled"):
        ch, cm = _parse_hh_mm(str(sched_cfg.get("prefetch_concepts_time") or "05:00"))
        sched.add_job(
            lambda: _job_prefetch_stock_concepts(sched_cfg),
            CronTrigger(timezone=tz, hour=ch, minute=cm),
            id="quant_prefetch_stock_concepts",
            **defaults,
        )

    logger.info(
        "[quant-scheduler] r3 已注册: news / pre / during×%d / lunch / maintain_daily / daily_decision",
        len(during_times),
    )
    return sched


def shutdown_quant_scheduler(sched: BackgroundScheduler) -> None:
    if sched.running:
        sched.shutdown(wait=False)
    logger.info("[quant-scheduler] 已停止")
