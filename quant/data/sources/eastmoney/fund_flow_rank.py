"""东财个股资金流排名（clist 分页）——自研分页，不改 akshare 原函数。

节流策略：
- 页间默认随机 29–61s；每成功 1–3 页批停 2–4 分钟
- 单页只打 1 次；失败后长停并跳过该页
- **连续失败 2 次**立即结束分页，由编排层回退逐票
- TLS：curl_cffi impersonate；Cookie 每页重读 ``.eastmoney.header``
"""

from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path

import pandas as pd

from common.utils.source_headers import load_headers_from_file
from quant.data.calendar import to_iso
from quant.store.paths import quant_home

_MIN_INTERVAL = 10.0  # 页间绝对下限（默认区间见 yml req_page_interval 29,61）
_MAX_INTERVAL = 61.0
_BURST_PAGES_MIN = 1
_BURST_PAGES_MAX = 3
_BATCH_PAUSE_MIN = 120.0
_BATCH_PAUSE_MAX = 240.0
_MAX_CONSECUTIVE_FAILS = 2
_IMPERSONATE_POOL = ("chrome120", "chrome124", "chrome131", "chrome110", "chrome")

_INDICATOR_MAP: dict[str, tuple[str, str, str]] = {
    "今日": (
        "f62",
        "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124",
        "f62",
    ),
    "3日": (
        "f267",
        "f12,f14,f2,f127,f267,f268,f269,f270,f271,f272,f273,f274,f275,f276,f257,f258,f124",
        "f267",
    ),
    "5日": (
        "f164",
        "f12,f14,f2,f109,f164,f165,f166,f167,f168,f169,f170,f171,f172,f173,f257,f258,f124",
        "f164",
    ),
    "10日": (
        "f174",
        "f12,f14,f2,f160,f174,f175,f176,f177,f178,f179,f180,f181,f182,f183,f260,f261,f124",
        "f174",
    ),
}

_FS = (
    "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,"
    "m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
)
_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_UT = "b2884a393a59ad64002292a3e90d46a5"

_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/zjlx/detail.html",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://data.eastmoney.com",
}

_LOG = "[fund_flow_rank]"
_DISCONNECT_MARKERS = (
    "remotedisconnected",
    "connection aborted",
    "connection reset",
    "broken pipe",
    "curl: (56)",
    "curl: (7)",
    "curl: (28)",
    "timed out",
    "timeout",
    "disconnected",
    "ssleof",
    "eof occurred",
    "network is unreachable",
)


def _em_proxies() -> dict[str, str | None] | None:
    try:
        from common.config import Settings

        settings = Settings()
    except Exception:  # noqa: BLE001
        return {"http": None, "https": None}
    if not settings.PROXY_ENABLED:
        return {"http": None, "https": None}
    px = settings.httpx_proxy_url()
    if not px:
        return {"http": None, "https": None}
    return {"http": px, "https": px}


def _progress_dir(as_of: str, indicator: str) -> Path:
    safe_ind = indicator.replace("/", "_")
    return quant_home() / "data" / "fund_flow_rank_progress" / to_iso(as_of) / safe_ind


def _meta_path(as_of: str, indicator: str) -> Path:
    return _progress_dir(as_of, indicator) / "meta.json"


def _pages_dir(as_of: str, indicator: str) -> Path:
    return _progress_dir(as_of, indicator) / "pages"


def _read_meta(as_of: str, indicator: str) -> dict:
    p = _meta_path(as_of, indicator)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_meta(as_of: str, indicator: str, meta: dict) -> None:
    p = _meta_path(as_of, indicator)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_page_frames(as_of: str, indicator: str, upto_page: int) -> list[pd.DataFrame]:
    root = _pages_dir(as_of, indicator)
    frames: list[pd.DataFrame] = []
    for pn in range(1, upto_page + 1):
        fp = root / f"page_{pn:04d}.parquet"
        if fp.is_file():
            frames.append(pd.read_parquet(fp))
    return frames


def _save_page(as_of: str, indicator: str, page: int, df: pd.DataFrame) -> None:
    root = _pages_dir(as_of, indicator)
    root.mkdir(parents=True, exist_ok=True)
    df.to_parquet(root / f"page_{page:04d}.parquet", index=False)


def _normalize(raw: pd.DataFrame, net_field: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["code", "main_net_inflow"])
    code = raw["f12"].astype(str).str.strip() if "f12" in raw.columns else ""
    net = (
        pd.to_numeric(raw[net_field], errors="coerce")
        if net_field in raw.columns
        else float("nan")
    )
    out = pd.DataFrame({"code": code, "main_net_inflow": net})
    out = out[out["code"].astype(bool) & out["main_net_inflow"].notna()]
    return out.drop_duplicates(subset=["code"], keep="last").reset_index(drop=True)


def load_cached_rank(as_of: str, indicator: str = "5日") -> pd.DataFrame | None:
    """读取断点已落盘页，拼成 DataFrame；无缓存返回 None。"""
    meta = _read_meta(as_of, indicator)
    last = int(meta.get("last_page") or 0)
    if last <= 0:
        return None
    frames = _load_page_frames(as_of, indicator, last)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["code"], keep="last")


def _is_disconnect(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(m in msg for m in _DISCONNECT_MARKERS)


def _rand_pause(lo: float, hi: float, *, what_next: str) -> None:
    lo = max(0.0, float(lo))
    hi = max(lo, float(hi))
    sec = random.uniform(lo, hi)
    print(
        f"{_LOG} 【休眠开始】约 {sec / 60:.1f} 分钟（{sec:.0f}s）· 进程未退出 · {what_next}",
        flush=True,
    )
    time.sleep(sec)
    print(f"{_LOG} 【休眠结束】{what_next}", flush=True)


def _fetch_page_once(
    *,
    fid: str,
    fields: str,
    page: int,
    page_size: int,
) -> tuple[pd.DataFrame, int]:
    from curl_cffi import requests as cf_requests

    params = {
        "fid": fid,
        "po": "1",
        "pz": str(page_size),
        "pn": str(page),
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "ut": _UT,
        "fs": _FS,
        "fields": fields,
        "_": str(int(time.time() * 1000)),
    }
    headers = {**_BASE_HEADERS, **load_headers_from_file()}
    imp = random.choice(_IMPERSONATE_POOL)
    r = cf_requests.get(
        _URL,
        params=params,
        headers=headers,
        timeout=60,
        proxies=_em_proxies(),
        impersonate=imp,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code} impersonate={imp}")
    data = r.json().get("data") or {}
    total = int(data.get("total") or 0)
    diff = data.get("diff") or []
    return pd.DataFrame(diff), total


def fetch_stock_fund_flow_rank(
    *,
    indicator: str = "5日",
    page_size: int = 100,
    page_interval: float | None = None,
    page_interval_min: float | None = None,
    page_interval_max: float | None = None,
    burst_pages_min: int | None = None,
    burst_pages_max: int | None = None,
    batch_pause_min_sec: float | None = None,
    batch_pause_max_sec: float | None = None,
    as_of: str | None = None,
    force: bool = False,
    return_meta: bool = False,
):
    """分页拉取 → DataFrame(code, main_net_inflow)。

    页间随机间隔；每 2–4 页批停 2–4 分钟；单页失败不重试，长停后跳过该页继续下一页。
    """
    if indicator not in _INDICATOR_MAP:
        raise ValueError(f"indicator 须为 {list(_INDICATOR_MAP)}，收到 {indicator!r}")
    fid, fields, net_field = _INDICATOR_MAP[indicator]
    page_size = max(1, int(page_size))

    if page_interval_min is None and page_interval_max is None:
        if page_interval is not None:
            lo = hi = max(_MIN_INTERVAL, float(page_interval))
        else:
            lo, hi = 29.0, _MAX_INTERVAL  # 与 yml req_page_interval 默认一致
    else:
        lo = max(_MIN_INTERVAL, float(page_interval_min if page_interval_min is not None else 29.0))
        hi = max(lo, float(page_interval_max if page_interval_max is not None else _MAX_INTERVAL))

    b_lo = int(burst_pages_min if burst_pages_min is not None else _BURST_PAGES_MIN)
    b_hi = int(burst_pages_max if burst_pages_max is not None else _BURST_PAGES_MAX)
    b_lo = max(1, b_lo)
    b_hi = max(b_lo, b_hi)

    p_lo = float(
        batch_pause_min_sec if batch_pause_min_sec is not None else _BATCH_PAUSE_MIN
    )
    p_hi = float(batch_pause_max_sec if batch_pause_max_sec is not None else _BATCH_PAUSE_MAX)
    p_lo = max(0.0, p_lo)
    p_hi = max(p_lo, p_hi)

    start_page = 1
    total_pages: int | None = None
    frames: list[pd.DataFrame] = []
    last_fail_reason = ""
    skipped_pages: list[int] = []
    last_ok_page = 0
    consecutive_fails = 0

    if as_of and not force:
        meta = _read_meta(as_of, indicator)
        if meta.get("done") and meta.get("indicator") == indicator:
            cached = _load_page_frames(as_of, indicator, int(meta.get("last_page") or 0))
            if cached:
                print(
                    f"{_LOG} 【断点命中·已完成】as_of={to_iso(as_of)} "
                    f"直接复用 {len(cached)} 页，不再打网",
                    flush=True,
                )
                out = pd.concat(cached, ignore_index=True).drop_duplicates(
                    subset=["code"], keep="last"
                )
                meta_out = {**meta, "aborted": False, "from_cache": True}
                return (out, meta_out) if return_meta else out
        if meta.get("next_page") and meta.get("indicator") == indicator:
            start_page = int(meta["next_page"])
            total_pages = int(meta["total_pages"]) if meta.get("total_pages") else None
            last_ok_page = int(meta.get("last_page") or 0)
            skipped_pages = [int(x) for x in (meta.get("skipped_pages") or [])]
            # 续传时按已尝试上界加载已有成功页（跳过页文件本就不存在）
            upto = max(last_ok_page, start_page - 1)
            frames = _load_page_frames(as_of, indicator, upto)
            print(
                f"{_LOG} 【断点续传】as_of={to_iso(as_of)} "
                f"已缓存 {len(frames)} 页 · 跳过页={skipped_pages or '-'} "
                f"→ 从第 {start_page} 页继续",
                flush=True,
            )

    print(
        f"{_LOG} 【分页开始】源=eastmoney.clist_rank indicator={indicator} "
        f"每页{page_size}条 · 页间隔{lo:.0f}~{hi:.0f}s · "
        f"每成功{b_lo}~{b_hi}页批停{p_lo:.0f}~{p_hi:.0f}s · "
        f"单页失败跳过；连续失败{_MAX_CONSECUTIVE_FAILS}次则结束分页改逐票",
        flush=True,
    )

    page = start_page
    first_request = True
    burst_target = random.randint(b_lo, b_hi)
    burst_ok = 0
    highest_attempted = start_page - 1

    while True:
        if total_pages is not None and page > total_pages:
            break

        if not first_request:
            gap = random.uniform(lo, hi)
            print(
                f"{_LOG} 【页间等待】{gap:.0f}s 后请求第 {page} 页",
                flush=True,
            )
            time.sleep(gap)
        first_request = False

        page_label = (
            f"第 {page}/{total_pages} 页" if total_pages is not None else f"第 {page} 页"
        )
        print(f"{_LOG} 【请求中】{page_label} …", flush=True)
        try:
            raw, total = _fetch_page_once(
                fid=fid, fields=fields, page=page, page_size=page_size
            )
        except Exception as exc:  # noqa: BLE001
            last_fail_reason = f"{type(exc).__name__}: {exc}"
            kind = "断连" if _is_disconnect(exc) else "失败"
            print(
                f"{_LOG} 【{page_label}{kind}】未落盘 · {last_fail_reason}",
                flush=True,
            )
            consecutive_fails += 1
            next_page = page + 1
            skipped_pages.append(page)
            highest_attempted = page
            if as_of:
                _write_meta(
                    as_of,
                    indicator,
                    {
                        "indicator": indicator,
                        "page_size": page_size,
                        "total_pages": total_pages,
                        "next_page": next_page,
                        "last_page": last_ok_page,
                        "skipped_pages": skipped_pages,
                        "done": False,
                        "aborted": consecutive_fails >= _MAX_CONSECUTIVE_FAILS,
                        "last_error": last_fail_reason,
                    },
                )
            if consecutive_fails >= _MAX_CONSECUTIVE_FAILS:
                print(
                    f"{_LOG} 【说明】连续失败 {consecutive_fails} 次 → 结束分页，回退逐票补全",
                    flush=True,
                )
                _rand_pause(
                    p_lo,
                    p_hi,
                    what_next="分页中止，进入逐票阶段前短暂冷却",
                )
                break
            print(
                f"{_LOG} 【说明】本页不重试（连续失败 {consecutive_fails}/"
                f"{_MAX_CONSECUTIVE_FAILS}）；长停后跳到下一页",
                flush=True,
            )
            _rand_pause(
                p_lo,
                p_hi,
                what_next=(
                    f"跳过第 {page} 页，改请求第 {next_page} 页"
                    if total_pages is None or next_page <= total_pages
                    else f"跳过第 {page} 页后分页探查结束"
                ),
            )
            if total_pages is not None and page >= total_pages:
                break
            page = next_page
            first_request = True
            continue

        consecutive_fails = 0
        if total_pages is None:
            total_pages = max(1, math.ceil(total / page_size)) if total else 1
            print(
                f"{_LOG} 【总量】全市场约 {total} 条 → 共 {total_pages} 页",
                flush=True,
            )

        norm = _normalize(raw, net_field)
        frames.append(norm)
        last_ok_page = page
        highest_attempted = page
        if as_of:
            _save_page(as_of, indicator, page, norm)
            _write_meta(
                as_of,
                indicator,
                {
                    "indicator": indicator,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": total_pages,
                    "next_page": page + 1,
                    "last_page": page,
                    "skipped_pages": skipped_pages,
                    "done": False,
                    "aborted": False,
                },
            )
        print(
            f"{_LOG} 【成功】第 {page}/{total_pages} 页 · 本页 {len(norm)} 码 · "
            f"累计 {sum(len(f) for f in frames)} 码（已断点落盘）"
            f"{(' · 已跳过页=' + str(skipped_pages)) if skipped_pages else ''}",
            flush=True,
        )

        burst_ok += 1
        finished = page >= total_pages or raw.empty
        if not finished and burst_ok >= burst_target:
            _rand_pause(
                p_lo,
                p_hi,
                what_next=(
                    f"批间休息结束：已连拉 {burst_ok} 页，"
                    f"接着从第 {page + 1} 页继续（下次批停阈值仍 {b_lo}~{b_hi} 页）"
                ),
            )
            burst_ok = 0
            burst_target = random.randint(b_lo, b_hi)
            first_request = True

        if finished:
            break
        page += 1

    if not frames:
        empty = pd.DataFrame(columns=["code", "main_net_inflow"])
        meta_out = {
            "indicator": indicator,
            "last_page": 0,
            "skipped_pages": skipped_pages,
            "aborted": True,
            "abort_reason": last_fail_reason or "no_pages",
            "done": False,
        }
        if as_of:
            _write_meta(as_of, indicator, meta_out)
        print(
            f"{_LOG} 【分页结束·无数据】尚未成功落盘任何页 · "
            f"跳过页={skipped_pages or '-'} · last_error={last_fail_reason or 'no_pages'} "
            f"→ 将由逐票补全",
            flush=True,
        )
        return (empty, meta_out) if return_meta else empty

    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["code"], keep="last")
    if as_of:
        meta_now = _read_meta(as_of, indicator)
        last_ok_page = int(meta_now.get("last_page") or last_ok_page)
        skipped_pages = [int(x) for x in (meta_now.get("skipped_pages") or skipped_pages)]

    finished_range = total_pages is not None and highest_attempted >= total_pages
    meta_out = {
        "indicator": indicator,
        "page_size": page_size,
        "total_pages": total_pages,
        "last_page": last_ok_page,
        "next_page": highest_attempted + 1,
        "skipped_pages": skipped_pages,
        "done": finished_range,
        "aborted": (not finished_range) or bool(skipped_pages),
        "abort_reason": (
            f"skipped_pages={skipped_pages}" if skipped_pages else (last_fail_reason or "")
        ),
        "n_codes": int(len(out)),
    }
    if as_of:
        _write_meta(as_of, indicator, meta_out)

    print(
        f"{_LOG} 【分页结束】{'页范围走完' if finished_range else '未走完'} · "
        f"成功至第 {last_ok_page} 页 / 总 {total_pages} · 去重 {len(out)} 码 · "
        f"跳过页={skipped_pages or '-'}"
        f"{'（缺码由逐票补）' if skipped_pages else ''}",
        flush=True,
    )
    return (out.reset_index(drop=True), meta_out) if return_meta else out.reset_index(drop=True)
