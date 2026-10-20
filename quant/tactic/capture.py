"""观察池数据：涨停池发现 5 板，只留这些票的日 K 和东财已触发的严重异动日。"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta

import pandas as pd

from quant.data.calendar import prev_trading_day, to_iso, trading_day_list
from quant.data.zt_pool import read_zt_pool, read_zt_pool_range, write_zt_pool_snapshot
from quant.store.paths import quant_home

_BAR_COLS = ("date", "open", "high", "low", "close", "volume", "name")
_SEVERE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_SEVERE_MARKS = ("100%", "150%", "200%", "300%", "-50%", "-60%", "-70%", "-75%", "4次", "3次", "严重异常")


def _root():
    return quant_home() / "store" / "tactic"


def bars_path(code: str):
    return _root() / "bars" / f"{code}.parquet"


def load_bars(code: str) -> pd.DataFrame:
    path = bars_path(code)
    if not path.is_file():
        return pd.DataFrame(columns=["code", *_BAR_COLS])
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame(columns=["code", *_BAR_COLS])
    if "code" not in df.columns:
        df = df.copy()
        df["code"] = code
    return df


def save_bars(code: str, df: pd.DataFrame) -> None:
    path = bars_path(code)
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = [c for c in ("code", *_BAR_COLS) if c in df.columns]
    df[keep].to_parquet(path, index=False)


def load_severe() -> dict[str, list[dict]]:
    path = _root() / "severe.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_severe(data: dict[str, list[dict]]) -> None:
    path = _root() / "severe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def latest_trigger(code: str, *, as_of: str) -> str | None:
    rows = load_severe().get(code) or []
    days = [str(r.get("date") or "")[:10] for r in rows]
    days = [d for d in days if d and d <= to_iso(as_of)]
    return max(days) if days else None


def _pause() -> None:
    time.sleep(0.35)


def capture_zt(as_of: str) -> pd.DataFrame:
    from quant.data.sources.interface import try_with_fallback

    day = to_iso(as_of)
    df = try_with_fallback("market", "fetch_zt_pool_hist", date=day)
    if df is None or getattr(df, "empty", True):
        raise RuntimeError(f"涨停池为空 {day}")
    write_zt_pool_snapshot(day, df)
    return df


def capture_side_pools(as_of: str) -> None:
    from quant.data.board_pools import KIND_IFACE, write_board_pool
    from quant.data.sources.interface import try_with_fallback

    day = to_iso(as_of)
    for kind, iface in KIND_IFACE.items():
        raw = try_with_fallback("market", iface, date=day)
        if not isinstance(raw, pd.DataFrame):
            raise RuntimeError(f"{kind} 池失败 {day}")
        write_board_pool(kind, day, raw)
        _pause()


def codes_ge5(start: str, end: str) -> set[str]:
    rec = read_zt_pool_range(start, end)
    if rec is None or rec.empty or "boards" not in rec.columns:
        return set()
    hi = rec[pd.to_numeric(rec["boards"], errors="coerce") >= 5]
    return {str(c).zfill(6)[-6:] for c in hi["code"].astype(str) if str(c).strip()}


def _name_map(start: str, end: str) -> dict[str, str]:
    rec = read_zt_pool_range(start, end)
    out: dict[str, str] = {}
    if rec is None or rec.empty:
        return out
    for _, r in rec.sort_values("date").iterrows():
        code = str(r.get("code") or "").zfill(6)[-6:]
        name = str(r.get("name") or "").strip()
        if code and name and name.lower() != "nan":
            out[code] = name
    return out


def upsert_hist(code: str, start: str, end: str, name: str = "") -> pd.DataFrame:
    from quant.data.sources.interface import try_with_fallback

    df = try_with_fallback("daily", "fetch_hist", code=code, start=start, end=end, adjust="")
    if df is None or getattr(df, "empty", True):
        raise RuntimeError(f"无日K {code}")
    d = df.copy()
    d["code"] = code
    d["date"] = d["date"].astype(str).str[:10]
    if name:
        d["name"] = name
    elif "name" not in d.columns:
        d["name"] = ""
    for c in ("open", "high", "low", "close", "volume"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    old = load_bars(code)
    if old is not None and not old.empty:
        d = pd.concat([old, d], ignore_index=True)
    d = d.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    save_bars(code, d)
    return d


def _severe_row(raw: dict) -> dict | None:
    code = str(raw.get("SECURITY_CODE") or "").strip().zfill(6)[-6:]
    why = str(raw.get("UNUSUAL_TYPE") or "")
    day = str(raw.get("TRADE_DATE") or "")[:10]
    if len(code) != 6 or not day or not any(m in why for m in _SEVERE_MARKS):
        return None
    return {"date": day, "type_name": why}


def fetch_severe_happened() -> dict[str, list[dict]]:
    import requests

    out: dict[str, list[dict]] = {}
    page = 1
    pages = 1
    while page <= pages and page <= 20:
        resp = requests.get(
            _SEVERE_URL,
            params={
                "reportName": "RPT_WATCH_UNUSUAL_FLUCTUATE",
                "columns": "SECURITY_CODE,TRADE_DATE,UNUSUAL_TYPE,IS_HAPPEN",
                "pageSize": "500",
                "pageNumber": str(page),
                "sortColumns": "TRADE_DATE",
                "sortTypes": "-1",
                "filter": '(IS_HAPPEN="1")',
            },
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("success"):
            raise RuntimeError(str(payload.get("message") or "严重异动历史失败"))
        result = payload.get("result") or {}
        pages = int(result.get("pages") or 1)
        for raw in result.get("data") or []:
            item = _severe_row(raw)
            if item is None:
                continue
            code = str(raw.get("SECURITY_CODE") or "").zfill(6)[-6:]
            bucket = out.setdefault(code, [])
            if item not in bucket:
                bucket.append(item)
        page += 1
    return out


def refresh_severe(codes: set[str]) -> None:
    got = fetch_severe_happened()
    cur = load_severe()
    for code in codes:
        cur[code] = got.get(code, [])
    save_severe(cur)


def recent_sessions(as_of: str, n: int = 40) -> list[date]:
    end = date.fromisoformat(to_iso(as_of))
    days = trading_day_list(end - timedelta(days=n * 2 + 10), end)
    return days[-n:]


def backfill(as_of: str, *, sessions: int = 40) -> dict:
    """按近若干交易日涨停池找出 ≥5 板，只补这些票的日 K 和严重异动触发日。"""
    day = to_iso(as_of)
    days = recent_sessions(day, sessions)
    if len(days) < 5:
        raise RuntimeError(f"交易日不足 {day}")
    kept: list[date] = []
    skipped: list[str] = []
    for d in days:
        try:
            capture_zt(d.isoformat())
            kept.append(d)
        except RuntimeError as e:
            if "为空" not in str(e):
                raise
            skipped.append(d.isoformat())
        _pause()
    if not kept or kept[-1].isoformat() != day:
        raise RuntimeError(f"最近交易日涨停池失败 {day}，跳过 {skipped[:8]}")
    prev = prev_trading_day(date.fromisoformat(day))
    for d in (prev, date.fromisoformat(day)):
        if d is None:
            continue
        capture_side_pools(d.isoformat())
    start = kept[0].isoformat()
    codes = codes_ge5(start, day)
    if not codes:
        raise RuntimeError(f"{start}~{day} 无 5 板")
    names = _name_map(start, day)
    hist_start = (days[0] - timedelta(days=50)).isoformat()
    failed: list[str] = []
    for code in sorted(codes):
        try:
            upsert_hist(code, hist_start, day, names.get(code, ""))
        except Exception as e:
            failed.append(f"{code} {e}")
        _pause()
    if len(failed) == len(codes):
        raise RuntimeError("日K全部失败: " + "; ".join(failed[:5]))
    refresh_severe(codes)
    return {"as_of": day, "sessions": len(kept), "codes": len(codes), "failed": failed, "skipped": skipped}


def ensure_close_inputs(as_of: str, extra_codes: set[str] | None = None) -> set[str]:
    """复盘前补当天池和相关日 K。池子失败直接抛出，避免用空池把票判成断板。"""
    day = to_iso(as_of)
    end = date.fromisoformat(day)
    if read_zt_pool(day).empty:
        capture_zt(day)
        _pause()
    prev = prev_trading_day(end)
    if prev is not None and read_zt_pool(prev.isoformat()).empty:
        capture_zt(prev.isoformat())
        _pause()
    from quant.data.board_pools import read_board_pool

    if any(read_board_pool(k, day).empty for k in ("zb", "dt", "prev")):
        capture_side_pools(day)
    days = recent_sessions(day, 40)
    if not days:
        raise RuntimeError(f"无交易日 {day}")
    codes = codes_ge5(days[0].isoformat(), day)
    codes.update(extra_codes or set())
    if not codes:
        raise RuntimeError(f"{day} 近 40 日无 5 板，且观察池为空")
    names = _name_map(days[0].isoformat(), day)
    hist_start = (days[0] - timedelta(days=50)).isoformat()
    failed: list[str] = []
    for code in sorted(codes):
        have = load_bars(code)
        last = ""
        if have is not None and not have.empty and "date" in have.columns:
            last = str(have["date"].max())[:10]
        if last >= day:
            continue
        try:
            upsert_hist(code, hist_start, day, names.get(code, ""))
            _pause()
        except Exception as e:
            failed.append(f"{code} {e}")
    if failed and len(failed) >= len(codes):
        raise RuntimeError("日K失败，本次复盘中止: " + "; ".join(failed[:5]))
    refresh_severe(codes)
    return codes
