"""炸板 / 跌停 / 昨日涨停 PIT。东财 push2ex 历史窗口短，按日覆盖写。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from quant.data.calendar import to_iso
from quant.store.paths import quant_home

KIND_STORE = {"zb": "zt_zb", "dt": "zt_dt", "prev": "zt_prev"}
KIND_IFACE = {
    "zb": "fetch_zt_zb_pool",
    "dt": "fetch_zt_dt_pool",
    "prev": "fetch_zt_prev_pool",
}

_COMMON = ("code", "date", "name", "pct", "close", "amount", "float_mv", "total_mv", "turnover", "industry")
_ZB_EXTRA = ("limit_px", "first_seal", "break_n", "amp", "speed", "zt_stat")
_DT_EXTRA = ("seal_amt", "last_seal", "board_amt", "dt_n", "open_n", "pe")
_PREV_EXTRA = ("limit_px", "speed", "amp", "y_seal", "y_boards", "zt_stat")
KIND_COLUMNS = {
    "zb": _COMMON + _ZB_EXTRA,
    "dt": _COMMON + _DT_EXTRA,
    "prev": _COMMON + _PREV_EXTRA,
}

_RENAME = {
    "代码": "code",
    "名称": "name",
    "涨跌幅": "pct",
    "最新价": "close",
    "成交额": "amount",
    "流通市值": "float_mv",
    "总市值": "total_mv",
    "换手率": "turnover",
    "所属行业": "industry",
    "涨停价": "limit_px",
    "首次封板时间": "first_seal",
    "最后封板时间": "last_seal",
    "昨日封板时间": "y_seal",
    "炸板次数": "break_n",
    "振幅": "amp",
    "涨速": "speed",
    "涨停统计": "zt_stat",
    "封单资金": "seal_amt",
    "板上成交额": "board_amt",
    "连续跌停": "dt_n",
    "开板次数": "open_n",
    "动态市盈率": "pe",
    "昨日连板数": "y_boards",
}


def _code6(s) -> str:
    t = str(s or "").strip()
    if t.endswith((".SZ", ".SH", ".BJ")):
        t = t.split(".")[0]
    digits = "".join(ch for ch in t if ch.isdigit())
    return digits.zfill(6) if digits else ""


def snapshot_path(kind: str, as_of: str) -> Path:
    day = to_iso(as_of)
    store = KIND_STORE[kind]
    return quant_home() / "store" / store / f"year={day[:4]}" / f"{day}.parquet"


def normalize_board_pool(df: pd.DataFrame | None, *, kind: str, as_of: str) -> pd.DataFrame:
    cols = list(KIND_COLUMNS[kind])
    empty = pd.DataFrame(columns=cols)
    day = to_iso(as_of)
    if df is None or df.empty:
        return empty
    d = df.copy().rename(columns=_RENAME)
    if "code" not in d.columns:
        return empty
    d["code"] = d["code"].map(_code6)
    d["date"] = day
    if "name" in d.columns:
        d["name"] = d["name"].astype(str)
    else:
        d["name"] = ""
    for c in cols:
        if c not in d.columns:
            d[c] = pd.NA
    d = d[cols].copy()
    d = d[d["code"].astype(str).str.len() == 6]
    for c in cols:
        if c in ("code", "date", "name", "industry", "first_seal", "last_seal", "y_seal", "zt_stat"):
            continue
        d[c] = pd.to_numeric(d[c], errors="coerce")
    return d.reset_index(drop=True)


def write_board_pool(kind: str, as_of: str, df: pd.DataFrame) -> int:
    day = to_iso(as_of)
    cols = list(KIND_COLUMNS[kind])
    d = df if df is not None else pd.DataFrame(columns=cols)
    if list(d.columns) != cols:
        d = normalize_board_pool(d, kind=kind, as_of=day)
    path = snapshot_path(kind, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    d.to_parquet(path, index=False)
    return int(len(d))


def read_board_pool(kind: str, as_of: str) -> pd.DataFrame:
    path = snapshot_path(kind, as_of)
    cols = list(KIND_COLUMNS[kind])
    if not path.is_file():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_parquet(path)
    except Exception:
        return pd.DataFrame(columns=cols)
    return normalize_board_pool(df, kind=kind, as_of=as_of) if not df.empty else df


def capture_board_pools(as_of: str) -> dict[str, int]:
    from quant.data.sources.interface import try_with_fallback

    as_of = to_iso(as_of)
    out: dict[str, int] = {}
    for kind, iface in KIND_IFACE.items():
        try:
            raw = try_with_fallback("market", iface, date=as_of)
        except Exception:
            raw = None
        n = write_board_pool(kind, as_of, raw if isinstance(raw, pd.DataFrame) else pd.DataFrame())
        out[kind] = n
    return out
