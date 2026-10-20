"""观察池个股的资金流与龙虎榜归档。策略不读，留给以后训练。"""

from __future__ import annotations

import asyncio

import pandas as pd

from quant.data.calendar import to_iso
from quant.data.sources.eastmoney.lhb import LHB_COLUMNS
from quant.store.paths import quant_home

FUND_COLUMNS = (
    "code",
    "date",
    "name",
    "main_net",
    "super_net",
    "big_net",
    "mid_net",
    "small_net",
    "main_pct",
)

_FUND_KEYS = {
    "main_net": ("主力净流入-净额", "main_net_inflow"),
    "super_net": ("超大单净流入-净额",),
    "big_net": ("大单净流入-净额",),
    "mid_net": ("中单净流入-净额",),
    "small_net": ("小单净流入-净额",),
    "main_pct": ("主力净流入-净占比",),
}


def _root():
    return quant_home() / "store" / "tactic" / "ml"


def _code6(s) -> str:
    digits = "".join(ch for ch in str(s or "") if ch.isdigit())
    return digits.zfill(6)[-6:] if digits else ""


def _num(row: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in row and row[k] is not None and row[k] != "":
            try:
                return float(row[k])
            except (TypeError, ValueError):
                continue
    return None


def _row_date(row: dict) -> str:
    for k in ("日期", "date"):
        if row.get(k):
            return str(row[k])[:10]
    return ""


def fund_row(code: str, as_of: str, name: str, rows: list[dict] | None) -> dict:
    hit = None
    for r in rows or []:
        if isinstance(r, dict) and _row_date(r) == as_of:
            hit = r
            break
    out = {"code": code, "date": as_of, "name": name}
    for col, keys in _FUND_KEYS.items():
        out[col] = _num(hit, keys) if hit else None
    return out


def absent_lhb(code: str, as_of: str, name: str) -> dict:
    row = {c: None for c in LHB_COLUMNS}
    row.update(code=code, date=as_of, name=name, reason="", on_list=False)
    return row


def _write(kind: str, as_of: str, df: pd.DataFrame) -> None:
    day = to_iso(as_of)
    path = _root() / kind / f"year={day[:4]}" / f"{day}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


async def _fund_rows(codes: list[str], names: dict[str, str], as_of: str) -> list[dict]:
    from quant.data.sources.interface import try_with_fallback_async

    out = []
    for code in codes:
        rows = None
        for attempt in range(2):
            try:
                rows = await try_with_fallback_async(
                    "enrich", "fetch_stock_fund_flow_daily", symbol=code, days=15
                )
            except Exception:
                rows = None
            if isinstance(rows, list) and rows:
                break
            if attempt == 0:
                await asyncio.sleep(1.5)
        out.append(fund_row(code, as_of, names.get(code, ""), rows if isinstance(rows, list) else None))
    return out


def codes_of_day(as_of: str, watch: list | None, positions: list | None) -> tuple[set[str], dict[str, str]]:
    """当天 5 板（涨停池）加上观察和持仓。"""
    from quant.data.zt_pool import read_zt_pool

    names: dict[str, str] = {}
    codes: set[str] = set()
    zt = read_zt_pool(as_of)
    if zt is not None and not zt.empty and "boards" in zt.columns:
        boards = pd.to_numeric(zt["boards"], errors="coerce")
        for _, r in zt[boards >= 5].iterrows():
            c = _code6(r.get("code"))
            if not c:
                continue
            codes.add(c)
            names[c] = str(r.get("name") or "")
    for w in list(watch or []) + list(positions or []):
        if not isinstance(w, dict):
            continue
        c = _code6(w.get("code"))
        if not c:
            continue
        codes.add(c)
        if w.get("name"):
            names[c] = str(w["name"])
    return codes, names


def record_day(as_of: str, codes: set[str], names: dict[str, str] | None = None) -> dict:
    """给这些票落当天资金流和龙虎榜。没上榜记 on_list=false。失败不抛。"""
    day = to_iso(as_of)
    names = names or {}
    wanted = sorted({_code6(c) for c in codes if _code6(c)})
    if not wanted:
        return {"fund": 0, "lhb": 0}
    try:
        fund = pd.DataFrame(asyncio.run(_fund_rows(wanted, names, day)))
        _write("fund", day, fund[list(FUND_COLUMNS)])
    except Exception as e:
        print(f"[ml] 资金流归档失败 {day}: {e}", flush=True)
        fund = None
    lhb_n = 0
    try:
        from quant.data.sources.interface import try_with_fallback

        raw = try_with_fallback("market", "fetch_lhb_day", date=day)
        listed = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(columns=list(LHB_COLUMNS))
        if listed.empty:
            print(f"[ml] 龙虎榜当日列表为空 {day}，不把观察池记成未上榜", flush=True)
            return {"fund": 0 if fund is None else len(fund), "lhb_rows": 0, "codes": len(wanted)}
        listed = listed[listed["code"].isin(wanted)]
        rows = listed.to_dict("records") if not listed.empty else []
        have = {str(r.get("code")) for r in rows}
        for code in wanted:
            if code not in have:
                rows.append(absent_lhb(code, day, names.get(code, "")))
        lhb = pd.DataFrame(rows)
        for c in LHB_COLUMNS:
            if c not in lhb.columns:
                lhb[c] = pd.NA
        _write("lhb", day, lhb[list(LHB_COLUMNS)])
        lhb_n = int(lhb["on_list"].fillna(False).sum()) if "on_list" in lhb.columns else 0
    except Exception as e:
        print(f"[ml] 龙虎榜归档失败 {day}: {e}", flush=True)
    return {"fund": 0 if fund is None else len(fund), "lhb_rows": lhb_n, "codes": len(wanted)}
