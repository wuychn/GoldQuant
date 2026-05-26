"""~/.quant/daily/{date}/ 按日归档。

子目录
  raw/      API 原始 payload（orchestrator save_raw）
  derived/  评分、信号、market_state（程序计算）
  trades/   executed.json 成交记录（ML 标签来源之一）
  review/   飞书正文 MD
"""

from __future__ import annotations

import json
import tempfile
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from quant.store.paths import daily_derived, daily_raw, daily_review, daily_trades, ensure_layout, today_str


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_raw(mode: str, payload: dict, *, now: datetime | None = None) -> Path:
    ensure_layout()
    d = today_str(now)
    name_map = {
        "news": "news.json",
        "pre_market": "pre_market.json",
        "during_market": "during_market.json",
        "post_market_lunch": "lunch.json",
        "post_market_evening": "evening.json",
    }
    filename = name_map.get(mode)
    if not filename:
        filename = f"{mode}.json"
    if mode == "during_market":
        ts = (now or datetime.now()).strftime("%H%M")
        filename = f"during_{ts}.json"
    path = daily_raw(filename, d)
    _write_json(path, payload)
    return path


def save_derived(name: str, data: Any, *, d: str | None = None) -> Path:
    ensure_layout()
    path = daily_derived(name, d or today_str())
    _write_json(path, data)
    return path


def save_review(mode: str, content: str, *, d: str | None = None) -> Path:
    ensure_layout()
    name_map = {
        "news": "news.md",
        "pre_market": "pre_market.md",
        "during_market": "during.md",
        "post_market_lunch": "lunch.md",
        "post_market_evening": "evening.md",
    }
    path = daily_review(name_map.get(mode, f"{mode}.md"), d or today_str())
    _write_text(path, content)
    return path


def daily_trades_path(name: str, d: str) -> Path:
    ensure_layout()
    return daily_trades(name, d)


def save_pnl_snapshot(pnl: dict, *, d: str | None = None) -> Path:
    return save_derived("pnl.json", pnl, d=d)
