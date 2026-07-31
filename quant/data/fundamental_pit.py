"""基本面 PIT 财务表：报告期财务 + 公告日闸门 + TTM 估值。

store/fundamental_pit/part.parquet 列：
  code, report_date, announce_date, roe, eps, bps, rev_yoy

PIT：``announce_date <= as_of``；估值：滚动 4 季 EPS 求 TTM，BPS 取最新报告期。
"""

from __future__ import annotations

import bisect
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from quant.data.calendar import to_iso
from quant.store.paths import quant_home

_PIT_COLS = ["code", "report_date", "announce_date", "roe", "eps", "bps", "rev_yoy"]


def pit_table_path() -> Path:
    return quant_home() / "store" / "fundamental_pit" / "part.parquet"


def regulatory_disclosure_deadline(report_date: str) -> str:
    """法定披露截止日（保守 PIT）。"""
    rd = to_iso(report_date)
    try:
        y = int(rd[:4])
        md = rd[5:10]
    except (ValueError, IndexError):
        return rd
    if md == "03-31":
        return f"{y}-04-30"
    if md == "06-30":
        return f"{y}-08-31"
    if md == "09-30":
        return f"{y}-10-31"
    if md == "12-31":
        return f"{y + 1}-04-30"
    return rd


def effective_announce_date(row: pd.Series | dict) -> str:
    """行级 PIT 闸门日。"""
    raw = row.get("announce_date") if isinstance(row, dict) else row.get("announce_date")
    if raw is not None and str(raw).strip() and str(raw)[:4].isdigit():
        s = to_iso(str(raw)[:10])
        if len(s) >= 10:
            return s
    rd = row.get("report_date") if isinstance(row, dict) else row.get("report_date")
    return regulatory_disclosure_deadline(str(rd))


def _parse_report_date_from_title(title: str) -> str | None:
    """从巨潮公告标题解析报告期（ISO）；含全称与简称。"""
    t = str(title)
    if any(x in t for x in ("摘要", "取消", "英文", "提示性", "更正", "修订")):
        return None
    rules: list[tuple[str, str]] = [
        (r"(20\d{2})年.*?年度报告", "-12-31"),
        (r"(20\d{2})年.*?半年度报告", "-06-30"),
        (r"(20\d{2})年.*?第一季度报告", "-03-31"),
        (r"(20\d{2})年.*?第三季度报告", "-09-30"),
        (r"(20\d{2})年(?!半).*?年报", "-12-31"),
        (r"(20\d{2})年?.*?半年报", "-06-30"),
        (r"(20\d{2})年?.*?一季报", "-03-31"),
        (r"(20\d{2})年?.*?三季报", "-09-30"),
        (r"(20\d{2})年报$", "-12-31"),
    ]
    for pat, suffix in rules:
        m = re.search(pat, t)
        if not m:
            continue
        y = m.group(1)
        if suffix == "-12-31" and "半年" in t:
            continue
        if suffix == "-03-31" and "第三" in t:
            continue
        return f"{y}{suffix}"
    return None


def _normalize_announce_str(raw) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()[:19]
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try:
        ts = pd.to_datetime(raw, errors="coerce")
        if pd.notna(ts):
            return ts.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


def _decompose_quarterly_eps(rows: list[dict | pd.Series]) -> list[tuple[str, float]]:
    """报告期累计 EPS → 单季 EPS（按 report_date 升序）。"""
    sorted_rows = sorted(rows, key=lambda r: str(r.get("report_date", ""))[:10])
    cumulative: dict[tuple[str, str], float] = {}
    quarterly: list[tuple[str, float]] = []
    for r in sorted_rows:
        rd = str(r.get("report_date", ""))[:10]
        if len(rd) < 10:
            continue
        try:
            eps = float(r.get("eps"))
            if eps != eps:
                continue
        except (TypeError, ValueError):
            continue
        y, md = rd[:4], rd[5:10]
        if md == "03-31":
            q = eps
        elif md == "06-30":
            prev = cumulative.get((y, "03-31"))
            q = eps - prev if prev is not None else eps * 0.5
        elif md == "09-30":
            prev = cumulative.get((y, "06-30"))
            q = eps - prev if prev is not None else eps / 3.0
        elif md == "12-31":
            prev = cumulative.get((y, "09-30"))
            q = eps - prev if prev is not None else eps
        else:
            continue
        cumulative[(y, md)] = eps
        quarterly.append((rd, q))
    return quarterly


def compute_ttm_eps(quarterly: list[tuple[str, float]]) -> float | None:
    """最近 4 个单季 EPS 之和；不足 4 季则年化或年报直用（含负值）。"""
    if not quarterly:
        return None
    qs = [q for _, q in quarterly]
    last_rd = quarterly[-1][0]
    if len(qs) >= 4:
        ttm = sum(qs[-4:])
    elif last_rd.endswith("12-31") and len(qs) == 1:
        ttm = qs[0]
    else:
        ttm = sum(qs) * (4.0 / len(qs))
    return float(ttm)


@dataclass
class PitIndex:
    """预构建 ``code → [(pit_gate, row)]``，bisect 查询 O(log n)。"""

    _by_code: dict[str, list[tuple[str, dict]]] = field(default_factory=dict)

    @classmethod
    def from_table(cls, table: pd.DataFrame) -> PitIndex:
        idx = cls()
        if table.empty:
            return idx
        df = table.copy()
        df["_pit_gate"] = df.apply(effective_announce_date, axis=1)
        for code, grp in df.groupby("code"):
            items: list[tuple[str, dict]] = []
            for _, r in grp.iterrows():
                items.append((str(r["_pit_gate"]), r.to_dict()))
            items.sort(key=lambda x: x[0])
            idx._by_code[str(code)] = items
        return idx

    def rows_as_of(self, code: str, as_of: str) -> list[dict]:
        as_of = to_iso(as_of)
        items = self._by_code.get(str(code), [])
        if not items:
            return []
        gates = [g for g, _ in items]
        n = bisect.bisect_right(gates, as_of)
        return [items[i][1] for i in range(n)]


def fetch_announce_map_for_code(code: str) -> dict[str, str]:
    """``report_date`` → 最早公告日（巨潮定期报告）。"""
    out: dict[str, str] = {}
    try:
        import akshare as ak
    except ImportError:
        return out
    end = datetime.now().strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=str(code).strip(),
            market="沪深京",
            category="定期报告",
            start_date="20180101",
            end_date=end,
        )
    except Exception:
        return out
    if df is None or df.empty:
        return out
    title_col = next((c for c in df.columns if "标题" in str(c)), None)
    time_col = next((c for c in df.columns if "时间" in str(c) or "日期" in str(c)), None)
    if not title_col or not time_col:
        return out
    for _, r in df.iterrows():
        rd = _parse_report_date_from_title(r.get(title_col, ""))
        ad = _normalize_announce_str(r.get(time_col))
        if not rd or not ad:
            continue
        prev = out.get(rd)
        if prev is None or ad < prev:
            out[rd] = ad
    return out


def write_fundamental_pit(rows: list[dict]) -> None:
    if not rows:
        return
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:
        raise ImportError("写 fundamental_pit 需要 pyarrow") from e
    path = pit_table_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    for c in _PIT_COLS:
        if c not in df.columns:
            df[c] = None
    df = df[_PIT_COLS]
    mask = df["announce_date"].isna() | (df["announce_date"].astype(str).str.len() < 8)
    df.loc[~mask, "announce_date"] = df.loc[~mask, "announce_date"].astype(str).str.slice(0, 10).map(to_iso)
    df.loc[mask, "announce_date"] = df.loc[mask, "report_date"].map(regulatory_disclosure_deadline)
    if path.is_file():
        old = pd.read_parquet(path)
        for c in _PIT_COLS:
            if c not in old.columns:
                old[c] = None
        if "announce_date" not in old.columns or old["announce_date"].isna().all():
            old["announce_date"] = old["report_date"].map(regulatory_disclosure_deadline)
        df = pd.concat([old[_PIT_COLS], df], ignore_index=True)
        df = df.drop_duplicates(subset=["code", "report_date"], keep="last")
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)


def read_fundamental_pit_table() -> pd.DataFrame:
    path = pit_table_path()
    if not path.is_file():
        return pd.DataFrame(columns=_PIT_COLS)
    try:
        df = pd.read_parquet(path)
        for c in ("report_date", "announce_date"):
            if c in df.columns:
                df[c] = df[c].astype(str).str.slice(0, 10)
        if "announce_date" not in df.columns or df["announce_date"].isna().all():
            df["announce_date"] = df["report_date"].map(regulatory_disclosure_deadline)
        else:
            mask = df["announce_date"].isna() | (df["announce_date"].astype(str).str.len() < 8)
            df.loc[mask, "announce_date"] = df.loc[mask, "report_date"].map(regulatory_disclosure_deadline)
        return df
    except Exception:
        return pd.DataFrame(columns=_PIT_COLS)


def _latest_pit_row(code: str, as_of: str, table: pd.DataFrame | None = None, index: PitIndex | None = None) -> dict | None:
    if index is not None:
        rows = index.rows_as_of(code, as_of)
        if not rows:
            return None
        r = rows[-1]
    else:
        as_of = to_iso(as_of)
        df = table if table is not None else read_fundamental_pit_table()
        if df.empty:
            return None
        sub = df[df["code"].astype(str) == str(code)].copy()
        if sub.empty:
            return None
        sub["_pit_gate"] = sub.apply(effective_announce_date, axis=1)
        sub = sub[sub["_pit_gate"].astype(str) <= as_of]
        if sub.empty:
            return None
        r = sub.sort_values("_pit_gate").iloc[-1].to_dict()
    out: dict = {}
    for k in ("roe", "eps", "bps", "rev_yoy"):
        try:
            v = float(r.get(k))
            if v == v:
                out[k] = v
        except (TypeError, ValueError):
            pass
    return out if out else None


def metrics_as_of(
    code: str,
    as_of: str,
    *,
    close: float | None = None,
    float_mv: float | None = None,
    table: pd.DataFrame | None = None,
    index: PitIndex | None = None,
) -> dict[str, float]:
    """PIT 基本面 + TTM 估值（``announce_date <= as_of``；``ep_ttm`` 带符号含亏损）。"""
    if index is None:
        if table is not None:
            index = PitIndex.from_table(table)
        else:
            index = PitIndex.from_table(read_fundamental_pit_table())

    pit_rows = index.rows_as_of(code, as_of)
    if not pit_rows:
        return {}

    latest = pit_rows[-1]
    out: dict[str, float] = {}
    for k in ("roe", "rev_yoy"):
        try:
            v = float(latest.get(k))
            if v == v:
                out[k] = v
        except (TypeError, ValueError):
            pass

    quarterly = _decompose_quarterly_eps(pit_rows)
    ttm_eps = compute_ttm_eps(quarterly)

    px = float(close) if close and close > 0 else None
    if px is not None and ttm_eps is not None:
        out["ep_ttm"] = ttm_eps / px
        if ttm_eps > 0:
            out["pe_ttm"] = px / ttm_eps

    try:
        bps = float(latest.get("bps"))
        if bps == bps and bps > 0 and px:
            out["pb"] = px / bps
            out["bp"] = bps / px
    except (TypeError, ValueError):
        pass

    return out


def fetch_financial_pit_for_code(code: str, *, announce_map: dict[str, str] | None = None) -> list[dict]:
    """从 AKShare 拉单票财务指标 + 公告日（供 build 脚本）。"""
    try:
        import akshare as ak
    except ImportError:
        return []
    amap = announce_map if announce_map is not None else fetch_announce_map_for_code(code)
    try:
        df = ak.stock_financial_analysis_indicator_em(symbol=str(code), start_year="2018")
    except Exception:
        return []
    if df is None or df.empty:
        return []
    date_col = next((c for c in df.columns if "日期" in str(c) or "报告" in str(c)), None)
    if date_col is None:
        return []
    rows: list[dict] = []
    for _, r in df.iterrows():
        rd = str(r.get(date_col, ""))[:10]
        if len(rd) == 8 and rd.isdigit():
            rd = f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}"
        if len(rd) < 10:
            continue
        ad = amap.get(rd) or regulatory_disclosure_deadline(rd)
        row: dict = {"code": str(code).strip(), "report_date": rd, "announce_date": ad}
        for src, dst in (
            ("净资产收益率(%)", "roe"),
            ("净资产收益率", "roe"),
            ("每股收益(元)", "eps"),
            ("每股收益", "eps"),
            ("每股净资产(元)", "bps"),
            ("每股净资产", "bps"),
            ("营业总收入同比增长(%)", "rev_yoy"),
            ("营业收入同比增长(%)", "rev_yoy"),
        ):
            if src in df.columns:
                try:
                    v = float(r[src])
                    if v == v:
                        row[dst] = v
                except (TypeError, ValueError):
                    pass
        if len(row) > 3:
            rows.append(row)
    return rows


def codes_in_pit_table() -> set[str]:
    df = read_fundamental_pit_table()
    if df.empty:
        return set()
    return set(df["code"].astype(str).tolist())


_AKSHARE_LISTING_CACHE: dict[str, str] | None = None


def fetch_listing_map_akshare() -> dict[str, str]:
    """交易所 A 股列表 + 上市日期（AKShare）。

    进程级缓存：IPO/上市日表在进程内静态，避免 ``listing_days_map`` 逐评估日
    重算时反复触发网络取数（曾导致 build_panel O(N_dates) 网络拉取）。
    """
    global _AKSHARE_LISTING_CACHE
    if _AKSHARE_LISTING_CACHE is not None:
        return _AKSHARE_LISTING_CACHE
    out: dict[str, str] = {}
    try:
        import akshare as ak
    except ImportError:
        return out
    try:
        df = ak.stock_info_a_code_name()
        if df is None or df.empty:
            return out
        code_col = "code" if "code" in df.columns else df.columns[0]
        for _, r in df.iterrows():
            c = str(r.get(code_col, "")).strip()
            if c:
                out[c] = out.get(c, "")
    except Exception:
        pass
    try:
        ipo = ak.stock_ipo_info()
        if ipo is not None and not ipo.empty:
            ccol = next((c for c in ipo.columns if "代码" in str(c)), None)
            dcol = next((c for c in ipo.columns if "上市" in str(c) and "日" in str(c)), None)
            if ccol and dcol:
                for _, r in ipo.iterrows():
                    c = str(r.get(ccol, "")).strip().zfill(6)
                    raw = str(r.get(dcol, "")).strip()[:10]
                    if len(raw) == 8 and raw.isdigit():
                        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
                    if c and len(raw) >= 10:
                        out[c] = raw[:10]
    except Exception:
        pass
    out = {c: d for c, d in out.items() if d}
    _AKSHARE_LISTING_CACHE = out
    return out


def fetch_with_retry(fn, *, retries: int = 3, sleep: float = 1.0):
    last = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i + 1 < retries:
                time.sleep(sleep * (i + 1))
    if last:
        raise last
    return None


def should_refresh_fundamental_pit(as_of: str) -> bool:
    """披露季后窗口：5 月(年报/Q1)、8 月下旬(H1)、9/11 月前半月(Q3/年报滞后)。"""
    from datetime import date

    d = date.fromisoformat(to_iso(as_of))
    if d.month == 5:
        return True
    if d.month == 8 and d.day >= 16:
        return True
    return d.month in (9, 11) and d.day <= 15


def _pit_meta_path(name: str) -> Path:
    return pit_table_path().parent / name


def refresh_already_ran_today(as_of: str) -> bool:
    """同日披露季刷新是否已跑过（避免整月每日空转）。"""
    path = _pit_meta_path("last_refresh.txt")
    if not path.is_file():
        return False
    try:
        return path.read_text(encoding="utf-8").strip() == to_iso(as_of)
    except Exception:
        return False


def mark_refresh_done(as_of: str) -> None:
    path = _pit_meta_path("last_refresh.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_iso(as_of), encoding="utf-8")


def _codes_for_disclosure_refresh(codes: list[str], limit: int) -> tuple[list[str], int]:
    """披露季 round-robin：每日限流拉全市场存量码（upsert 幂等）。"""
    n = len(codes)
    if n == 0:
        return [], 0
    cursor_path = _pit_meta_path("refresh_cursor.txt")
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    start = 0
    if cursor_path.is_file():
        try:
            start = int(cursor_path.read_text(encoding="utf-8").strip()) % n
        except Exception:
            start = 0
    if limit <= 0 or limit >= n:
        pending = list(codes)
        next_cursor = 0
    else:
        pending = [codes[(start + i) % n] for i in range(limit)]
        next_cursor = (start + limit) % n
    cursor_path.write_text(str(next_cursor), encoding="utf-8")
    return pending, next_cursor


def refresh_fundamental_pit_incremental(
    *,
    limit: int = 300,
    sleep: float = 0.3,
    retries: int = 2,
    new_codes_only: bool = False,
    rotate: bool = False,
    codes: list[str] | None = None,
) -> tuple[int, int]:
    """刷新 fundamental_pit。

    - ``new_codes_only=True``：仅补表内尚无记录的码（``build_fundamental_pit --resume``）。
    - ``rotate=True``：披露季 round-robin 重拉存量码（``update_daily``）。
    - 否则：顺序拉取（全量构建 ``build_fundamental_pit``）。
    """
    from quant.data.adjust import load_adjusted_daily

    if codes is None:
        daily = load_adjusted_daily()
        codes = sorted(daily["code"].astype(str).unique().tolist())
    else:
        codes = sorted({str(c).strip() for c in codes if str(c).strip()})
    if new_codes_only:
        done = codes_in_pit_table()
        pending = [c for c in codes if c not in done]
        if limit > 0:
            pending = pending[:limit]
    elif rotate:
        pending, _ = _codes_for_disclosure_refresh(codes, limit)
    else:
        pending = codes if limit <= 0 else codes[:limit]
    ok, fail = 0, 0
    batch: list[dict] = []
    for code in pending:
        rows: list[dict] = []
        for attempt in range(retries):
            try:
                rows = fetch_financial_pit_for_code(code)
                ok += 1
                break
            except Exception:
                if attempt + 1 >= retries:
                    fail += 1
                else:
                    time.sleep(sleep * (attempt + 1))
        batch.extend(rows)
        if len(batch) >= 500:
            write_fundamental_pit(batch)
            batch = []
        if sleep > 0:
            time.sleep(sleep)
    if batch:
        write_fundamental_pit(batch)
    return ok, fail
