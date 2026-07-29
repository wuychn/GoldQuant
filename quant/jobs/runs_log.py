"""Job 运行台账 runs.jsonl。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from quant.store.paths import reports_dir


def _payload_hash(payload: Any) -> str:
    try:
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = str(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def append_run_record(
    *,
    mode: str,
    started_at: datetime,
    duration_ms: int,
    ok: bool,
    degraded: list[str] | None = None,
    payload: Any = None,
    trades_executed: int = 0,
    trades_rejected: dict[str, str] | None = None,
) -> None:
    path = reports_dir("ops") / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "mode": mode,
        "started_at": started_at.astimezone(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "duration_ms": duration_ms,
        "ok": ok,
        "degraded": degraded or [],
        "payload_hash": _payload_hash(payload) if payload is not None else "",
        "trades_executed": trades_executed,
        "trades_rejected": trades_rejected or {},
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
