"""In-process 轻推送 job 入口。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from common.config import get_settings
from quant.data_fetch import load_mode_fixture, fixture_mode
from quant.jobs.runs_log import append_run_record
from quant.ops.modes import (
    build_during_body,
    build_evening_body,
    build_lunch_body,
    build_news_body,
    build_pre_market_body,
)
from quant.ops.push import push_text
from common.progress_log import log_progress, log_progress_done, log_progress_error
from quant.services.market.payload import build_mode_payload_async

_LABELS = {
    "news": "新闻聚焦",
    "pre_market": "开盘啦",
    "during_market": "智能盯盘",
    "post_market_lunch": "午间复盘",
    "post_market_evening": "收盘复盘",
}

_BUILDERS = {
    "news": build_news_body,
    "pre_market": build_pre_market_body,
    "during_market": build_during_body,
    "post_market_lunch": build_lunch_body,
    "post_market_evening": build_evening_body,
}


def _critical_failure_push(mode: str, reason: str) -> str:
    label = _LABELS.get(mode, mode)
    msg = f"⚠️ {label} job 失败: {reason}"
    try:
        from quant.push.feishu import get_token, send_msg

        send_msg(msg, get_token())
    except Exception as e:
        log_progress_error(mode, "飞书告警失败", detail=str(e))
    return msg


async def run_market_job_async(mode: str, *, push: bool = True) -> str:
    if mode not in _BUILDERS:
        raise ValueError(f"未知模式: {mode}")
    label = _LABELS[mode]
    settings = get_settings()
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    log_progress(mode, f"开始 {label} (in-process)")

    degraded: list[str] = []
    trades_executed = 0
    trades_rejected: dict[str, str] = {}
    ok = True
    payload: dict | list = {}
    raw_for_body: dict = {}

    try:
        if fixture_mode():
            raw = load_mode_fixture(mode)
            from quant.data_fetch import unwrap_payload

            payload = unwrap_payload(raw)
            raw_for_body = raw
        else:
            br = await build_mode_payload_async(mode, settings)
            if isinstance(br.payload, list):
                payload = br.payload
                raw_for_body: dict = {"code": 0, "message": "ok", "data": br.payload}
            else:
                payload = br.payload if isinstance(br.payload, dict) else {}
                raw_for_body = {"code": 0, "message": "ok", "data": payload}
            degraded = br.degraded
            if br.session:
                trades_executed = br.session.trades_executed
                trades_rejected = br.session.trades_rejected
                if not br.session.ok:
                    ok = False
                    _critical_failure_push(mode, br.session.error or "intraday 异常")
            if mode == "during_market" and payload.get("大盘指数") is None:
                ok = False
                _critical_failure_push(mode, "spot/大盘指数不可用")
    except Exception as e:
        ok = False
        log_progress_error(mode, "payload 构建失败", detail=str(e))
        _critical_failure_push(mode, str(e))
        append_run_record(
            mode=mode,
            started_at=started,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            ok=False,
            degraded=degraded,
            trades_executed=trades_executed,
            trades_rejected=trades_rejected,
        )
        raise

    try:
        if ok:
            if mode == "news":
                body = _BUILDERS[mode](raw_for_body)
            else:
                body = _BUILDERS[mode]({"code": 0, "message": "ok", "data": payload})
        else:
            body = _critical_failure_push(mode, "见上文")
    except Exception as e:
        log_progress_error(mode, "正文生成失败", detail=str(e))
        raise

    msg = push_text(label, body, mode=mode, push=push)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    append_run_record(
        mode=mode,
        started_at=started,
        duration_ms=duration_ms,
        ok=ok,
        degraded=degraded,
        payload=payload,
        trades_executed=trades_executed,
        trades_rejected=trades_rejected,
    )
    log_progress_done(mode, f"{label} 完成 ({duration_ms}ms)")
    return msg


def run_market_job(mode: str, *, push: bool = True) -> str:
    import asyncio

    return asyncio.run(run_market_job_async(mode, push=push))
