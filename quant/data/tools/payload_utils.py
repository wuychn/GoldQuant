"""Payload normalization / merge helpers (from quant_endpoint)."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from numbers import Integral, Real
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quant.data.calendar import _coerce_date
from quant.data.tools.datetime_norm import (
    normalize_quant_datetime_string,
    oldest_trading_day_in_window,
    should_normalize_datetime_like_string,
    yyyymmdd_to_iso,
)
from quant.pool.board_normalize import normalize_industry_board_rows
from quant.store.paths import quant_home
from quant.testing.trim import maybe_trim_for_test_phase, test_phase_list_limit

_SH_TZ = ZoneInfo("Asia/Shanghai")


def normalize_quant_datetimes(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: normalize_quant_datetimes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_quant_datetimes(v) for v in obj]
    if isinstance(obj, str) and should_normalize_datetime_like_string(obj):
        return normalize_quant_datetime_string(obj)
    return obj


def round_floats_for_api(obj: Any, *, ndigits: int = 2) -> Any:
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, Integral) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, Real) and not isinstance(obj, bool):
        return round(float(obj), ndigits)
    if isinstance(obj, dict):
        return {k: round_floats_for_api(v, ndigits=ndigits) for k, v in obj.items()}
    if isinstance(obj, list):
        return [round_floats_for_api(v, ndigits=ndigits) for v in obj]
    if isinstance(obj, tuple):
        return tuple(round_floats_for_api(v, ndigits=ndigits) for v in obj)
    return obj


def finalize_quant_payload(obj: Any) -> Any:
    try:
        cloned = copy.deepcopy(obj)
    except Exception:
        cloned = obj
    out = round_floats_for_api(normalize_quant_datetimes(cloned))
    if isinstance(out, dict):
        from quant.store.state import merge_payload_holdings

        out = merge_payload_holdings(out)
        out.pop("_observe_enriched", None)
    return maybe_trim_for_test_phase(out)


def merge_concept_boards(
    jzf: list | None,
    jzj: list | None,
    jdf: list | None,
    jzjlc: list | None,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    cap = test_phase_list_limit(default=limit)
    return {
        "涨幅榜": (jzf or [])[:cap],
        "跌幅榜": (jdf or [])[:cap],
        "资金流入榜": (jzj or [])[:cap],
        "资金流出榜": (jzjlc or [])[:cap],
    }


def merge_industry_boards(
    gain: list | None,
    fund: list | None,
    loss: list | None = None,
    fund_out: list | None = None,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    cap = test_phase_list_limit(default=limit)
    boards: dict[str, Any] = {
        "涨幅榜": normalize_industry_board_rows((gain or [])[:cap]),
        "资金流入榜": normalize_industry_board_rows((fund or [])[:cap]),
    }
    if loss is not None:
        boards["跌幅榜"] = normalize_industry_board_rows((loss or [])[:cap])
    if fund_out is not None:
        boards["资金流出榜"] = normalize_industry_board_rows((fund_out or [])[:cap])
    return boards


def row_date_yyyymmdd(row: dict, *, date_key: str = "日期") -> str | None:
    v = row.get(date_key)
    if v is None:
        return None
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%Y%m%d")
        except Exception:
            pass
    s = str(v).strip().replace("-", "").replace("/", "")[:8]
    if len(s) >= 8 and s[:8].isdigit():
        return s[:8]
    return None


def rows_last_n_trade_days(rows: list, *, n: int, date_key: str = "日期") -> list:
    if not isinstance(rows, list) or not rows or n <= 0:
        return []
    dated: list[tuple[str, dict]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d = row_date_yyyymmdd(r, date_key=date_key)
        if d:
            dated.append((d, r))
    if not dated:
        return list(rows[-n:]) if len(rows) >= n else list(rows)
    dated.sort(key=lambda x: x[0])
    anchor = dated[-1][0]
    iso = yyyymmdd_to_iso(anchor)
    if not iso:
        return [r for _, r in dated[-n:]]
    anchor_date = _coerce_date(iso)
    if anchor_date is None:
        return [r for _, r in dated[-n:]]
    oldest = oldest_trading_day_in_window(anchor_date, n)
    if oldest is None:
        return [r for _, r in dated[-n:]]
    oldest_s = oldest.strftime("%Y%m%d")
    return [r for d, r in dated if oldest_s <= d <= anchor]


def zt_height(pool: list[dict[str, Any]] | None):
    if not pool:
        return None
    mx = 0
    for r in pool:
        v = r.get("连板数")
        try:
            if v is not None and v != "":
                mx = max(mx, int(float(v)))
        except (TypeError, ValueError):
            continue
    return mx


def theme_payload_stub(gn_bk: dict | None, hy_bk: dict | None = None) -> dict[str, Any]:
    return {"概念板块": gn_bk or {}, "行业板块": hy_bk or {}}


def quant_data_file(name: str) -> Path:
    return quant_home() / name


def parse_jsonl_stock_text(text: str) -> list:
    raw: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            for it in obj:
                if isinstance(it, dict):
                    raw.append(it)
        elif isinstance(obj, dict):
            raw.append(obj)
    return normalize_quant_stock_rows(raw)


def normalize_quant_stock_rows(raw: list | None) -> list:
    if not raw:
        return []
    out: list = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = item.get("股票代码")
        if code is None or str(code).strip() == "":
            continue
        row = dict(item)
        row["股票代码"] = str(code).strip()
        out.append(row)
    return out


def load_stock_rows_from_quant_file(filename: str) -> list:
    path = quant_data_file(filename)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return parse_jsonl_stock_text(text)


def combine_cls_publish_datetime(pub_date_val: object, pub_time_val: object) -> str:
    ds = str(pub_date_val or "").strip()
    ts = str(pub_time_val or "").strip()
    day = ""
    if "T" in ds:
        day = ds.split("T")[0].replace("/", "-")[:10]
    elif re.match(r"^\d{4}-\d{2}-\d{2}", ds):
        day = ds[:10]
    if not day or len(day) < 10:
        day = datetime.now(_SH_TZ).strftime("%Y-%m-%d")
    ts = ts.replace("：", ":").strip()
    if not ts:
        return f"{day} 00:00:00"
    parts = [p for p in ts.split(":") if p != ""]
    try:
        if len(parts) >= 3:
            return f"{day} {int(parts[0]):02d}:{int(parts[1]):02d}:{int(parts[2]):02d}"
        if len(parts) == 2:
            return f"{day} {int(parts[0]):02d}:{int(parts[1]):02d}:00"
    except (ValueError, IndexError):
        pass
    return f"{day} {ts}"
