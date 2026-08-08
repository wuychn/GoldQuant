"""5 日主力净流入取数编排（数据层）。

1. ``market.fetch_stock_fund_flow_rank``（yml 接口级换源）分页，断点续传  
2. 分页结束后，对缺码走 ``enrich.fetch_stock_fund_flow_daily`` 逐票（跳过已有）  
断点：``fund_flow_rank_progress/{date}/`` 下 pages+meta 与 per_symbol*.json
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from quant.data.calendar import to_iso
from quant.store.paths import quant_home

_LOG = "[fund_flow_5d]"
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
)


def _cfg() -> dict:
    from quant.config import load_quant_config

    return load_quant_config().get("data") or {}


def _parse_cfg_range(csv_key: str, default: tuple[float, float]) -> tuple[float, float]:
    from common.utils.source_headers import parse_interval_range

    return parse_interval_range(_cfg().get(csv_key), default=default)


def _page_interval_bounds() -> tuple[float, float]:
    """分页页间间隔（yml ``req_page_interval``）。"""
    lo, hi = _parse_cfg_range("req_page_interval", (29.0, 61.0))
    lo = max(10.0, lo)
    return lo, max(lo, hi)


def _symbol_interval_bounds() -> tuple[float, float]:
    """逐票间隔（yml ``req_symbol_interval``）。"""
    lo, hi = _parse_cfg_range("req_symbol_interval", (10.0, 20.0))
    lo = max(10.0, lo)
    return lo, max(lo, hi)


def _batch_pause_bounds() -> tuple[float, float]:
    lo, hi = _parse_cfg_range("req_batch_pause", (120.0, 240.0))
    lo = max(0.0, lo)
    return lo, max(lo, hi)


def _burst_pages() -> tuple[int, int]:
    lo, hi = _parse_cfg_range("req_burst_pages", (1.0, 3.0))
    lo_i, hi_i = max(1, int(lo)), max(1, int(hi))
    return (lo_i, hi_i) if hi_i >= lo_i else (hi_i, lo_i)


def _rank_source_label() -> str:
    cfg = (_cfg().get("sources") or {}).get("market", "default")
    if isinstance(cfg, dict):
        val = cfg.get("fetch_stock_fund_flow_rank", "default")
    else:
        val = cfg
    if isinstance(val, list):
        return "+".join(str(x) for x in val)
    return str(val or "default")


def _per_symbol_dir(as_of: str) -> Path:
    return quant_home() / "data" / "fund_flow_rank_progress" / to_iso(as_of)


def _read_per_symbol_done(as_of: str) -> set[str]:
    p = _per_symbol_dir(as_of) / "per_symbol.json"
    if not p.is_file():
        return set()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {str(c).strip() for c in (raw.get("done") or []) if str(c).strip()}


def _write_per_symbol_state(as_of: str, done: set[str], values: dict[str, float]) -> None:
    d = _per_symbol_dir(as_of)
    d.mkdir(parents=True, exist_ok=True)
    (d / "per_symbol.json").write_text(
        json.dumps({"date": to_iso(as_of), "done": sorted(done), "n": len(done)}, ensure_ascii=False),
        encoding="utf-8",
    )
    (d / "per_symbol_values.json").write_text(
        json.dumps(values, ensure_ascii=False), encoding="utf-8"
    )


def _read_per_symbol_values(as_of: str) -> dict[str, float]:
    p = _per_symbol_dir(as_of) / "per_symbol_values.json"
    if not p.is_file():
        return {}
    try:
        return {str(k): float(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return {}


def _is_disconnect(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(m in msg for m in _DISCONNECT_MARKERS)


@dataclass
class FundFlow5dResult:
    df: pd.DataFrame
    sources_used: list[str] = field(default_factory=list)
    rank_pages_ok: int = 0
    rank_aborted: bool = False
    rank_abort_reason: str = ""
    per_symbol_ok: int = 0
    per_symbol_fail: int = 0


def fetch_main_net_inflow_5d(
    *,
    as_of: str,
    codes: list[str] | None = None,
    page_size: int | None = None,
    page_interval_min: float | None = None,
    page_interval_max: float | None = None,
    symbol_interval_min: float | None = None,
    symbol_interval_max: float | None = None,
    batch_pause_min_sec: float | None = None,
    batch_pause_max_sec: float | None = None,
    burst_pages_min: int | None = None,
    burst_pages_max: int | None = None,
    force: bool = False,
) -> FundFlow5dResult:
    """取 5 日主力净流入（元）。先分页（接口级换源），完成后逐票补缺。"""
    from quant.data.sources.interface import try_with_fallback

    page_lo, page_hi = _page_interval_bounds()
    if page_interval_min is not None:
        page_lo = max(10.0, float(page_interval_min))
    if page_interval_max is not None:
        page_hi = max(page_lo, float(page_interval_max))
    else:
        page_hi = max(page_lo, page_hi)

    sym_lo, sym_hi = _symbol_interval_bounds()
    if symbol_interval_min is not None:
        sym_lo = max(10.0, float(symbol_interval_min))
    if symbol_interval_max is not None:
        sym_hi = max(sym_lo, float(symbol_interval_max))
    else:
        sym_hi = max(sym_lo, sym_hi)

    pause_lo, pause_hi = _batch_pause_bounds()
    if batch_pause_min_sec is not None:
        pause_lo = max(0.0, float(batch_pause_min_sec))
    if batch_pause_max_sec is not None:
        pause_hi = max(pause_lo, float(batch_pause_max_sec))
    else:
        pause_hi = max(pause_lo, pause_hi)
    burst_lo, burst_hi = _burst_pages()
    if burst_pages_min is not None:
        burst_lo = max(1, int(burst_pages_min))
    if burst_pages_max is not None:
        burst_hi = max(burst_lo, int(burst_pages_max))
    else:
        burst_hi = max(burst_lo, burst_hi)
    data = _cfg()
    if page_size is None:
        page_size = int(data.get("fund_flow_rank_page_size", 100))

    need = {str(c).strip() for c in (codes or []) if str(c).strip()}
    rank_label = f"market.fetch_stock_fund_flow_rank[{_rank_source_label()}]"
    sources: list[str] = []

    print(
        f"{_LOG} 【开始】as_of={to_iso(as_of)} · 目标 {len(need) if need else '全市场'} 码",
        flush=True,
    )
    print(
        f"{_LOG} 【阶段1/2·分页】源={rank_label} · 页间隔{page_lo:.0f}~{page_hi:.0f}s · "
        f"每{burst_lo}~{burst_hi}页批停{pause_lo:.0f}~{pause_hi:.0f}s · "
        f"单页失败长停后跳过该页，缺码由阶段2逐票补（属正常，不是退出）",
        flush=True,
    )

    rank_df = pd.DataFrame(columns=["code", "main_net_inflow"])
    rank_aborted = False
    rank_reason = ""
    pages_ok = 0

    try:
        out = try_with_fallback(
            "market",
            "fetch_stock_fund_flow_rank",
            indicator="5日",
            page_size=page_size,
            page_interval_min=page_lo,
            page_interval_max=page_hi,
            burst_pages_min=burst_lo,
            burst_pages_max=burst_hi,
            batch_pause_min_sec=pause_lo,
            batch_pause_max_sec=pause_hi,
            as_of=as_of,
            force=force,
            return_meta=True,
        )
        if isinstance(out, tuple) and len(out) == 2:
            rank_df, meta = out
        else:
            rank_df, meta = out, {}
        pages_ok = int(meta.get("last_page") or 0)
        rank_aborted = bool(meta.get("aborted"))
        rank_reason = str(meta.get("abort_reason") or "")
        sources.append(rank_label)
        print(
            f"{_LOG} 【阶段1/2·分页结束】源={rank_label} · "
            f"{'完成' if not rank_aborted else '未完成'} · "
            f"{pages_ok} 页 · {len(rank_df)} 码"
            f"{(' · ' + rank_reason) if rank_reason else ''}",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        rank_aborted = True
        rank_reason = f"{type(exc).__name__}: {exc}"
        sources.append(rank_label)
        print(
            f"{_LOG} 【阶段1/2·分页异常】源={rank_label} · {rank_reason} → 尝试读断点缓存",
            flush=True,
        )
        from quant.data.sources.eastmoney.fund_flow_rank import _read_meta, load_cached_rank

        cached = load_cached_rank(as_of, "5日")
        if cached is not None and not cached.empty:
            rank_df = cached
            pages_ok = int(_read_meta(as_of, "5日").get("last_page") or 0)
            print(
                f"{_LOG} 【断点缓存】已恢复 {pages_ok} 页 · {len(rank_df)} 码",
                flush=True,
            )
        else:
            print(f"{_LOG} 【断点缓存】无可用分页数据", flush=True)

    have = {
        str(r["code"]).strip(): float(r["main_net_inflow"])
        for _, r in rank_df.iterrows()
        if str(r.get("code", "")).strip() and r.get("main_net_inflow") == r.get("main_net_inflow")
    }

    def _result_from_have(
        *,
        per_ok: int = 0,
        per_fail: int = 0,
        src: list[str] | None = None,
    ) -> FundFlow5dResult:
        if need:
            rows = [{"code": c, "main_net_inflow": have[c]} for c in sorted(need) if c in have]
        else:
            rows = [{"code": c, "main_net_inflow": v} for c, v in sorted(have.items())]
        return FundFlow5dResult(
            df=pd.DataFrame(rows, columns=["code", "main_net_inflow"]),
            sources_used=list(src or sources),
            rank_pages_ok=pages_ok,
            rank_aborted=rank_aborted,
            rank_abort_reason=rank_reason,
            per_symbol_ok=per_ok,
            per_symbol_fail=per_fail,
        )

    if not need:
        if not have:
            print(
                f"{_LOG} 【结束】无分页数据且未指定 codes，跳过逐票 · 返回空 "
                f"（可用 --fund-flow-mode per_symbol）",
                flush=True,
            )
        return _result_from_have()

    if not force:
        have.update(_read_per_symbol_values(as_of))

    missing = sorted(c for c in need if c not in have)
    if not missing:
        print(
            f"{_LOG} 【阶段2/2·跳过】目标已覆盖 {len(need)}/{len(need)}，无需逐票",
            flush=True,
        )
        return _result_from_have()

    print(
        f"{_LOG} 【阶段2/2·逐票补缺】源=enrich.fetch_stock_fund_flow_daily · "
        f"逐票间隔{sym_lo:.0f}~{sym_hi:.0f}s · "
        f"待补 {len(missing)} · 已有 {len(need) - len(missing)} "
        f"（分页已有码不重拉；断连休眠后重试同码）",
        flush=True,
    )
    sources.append("enrich.fetch_stock_fund_flow_daily")

    ok_n, fail_n, filled = _fill_per_symbol(
        as_of=as_of,
        codes=missing,
        interval_lo=sym_lo,
        interval_hi=sym_hi,
        pause_lo=pause_lo,
        pause_hi=pause_hi,
        force=force,
    )
    have.update(filled)
    covered = len(set(have) & need)
    print(
        f"{_LOG} 【阶段2/2·逐票结束】成功 {ok_n} · 失败/空 {fail_n} · "
        f"最终覆盖 {covered}/{len(need)}",
        flush=True,
    )
    return _result_from_have(per_ok=ok_n, per_fail=fail_n)


def _sum_main_net_5d(recs: list) -> float | None:
    """资金流日线近 5 根主力净流入合计（元）；不足 5 根或全 0 返回 None。"""
    recs = recs or []
    if len(recs) < 5:
        return None
    total = 0.0
    for r in recs[-5:]:
        v = r.get("main_net_inflow")
        if v is None:
            continue
        try:
            total += float(v)
        except (TypeError, ValueError):
            continue
    return total if total != 0 else None


def _fill_per_symbol(
    *,
    as_of: str,
    codes: list[str],
    interval_lo: float,
    interval_hi: float,
    pause_lo: float,
    pause_hi: float,
    force: bool,
) -> tuple[int, int, dict[str, float]]:
    from common.utils.source_headers import apply_source_header_patch, set_eastmoney_interval
    from quant.data.sources.interface import try_with_fallback_async

    apply_source_header_patch()
    set_eastmoney_interval(max(10, int(interval_lo)), max(10, int(interval_hi)))

    done = set() if force else _read_per_symbol_done(as_of)
    filled = {} if force else _read_per_symbol_values(as_of)
    pending = [c for c in codes if c not in done and c not in filled]
    if len(codes) - len(pending):
        print(
            f"{_LOG} 【逐票断点】跳过已完成 {len(codes) - len(pending)} · 待拉 {len(pending)}",
            flush=True,
        )

    ok_n = 0
    fail_n = 0

    async def _pull(code: str):
        return await try_with_fallback_async(
            "enrich", "fetch_stock_fund_flow_daily", symbol=code, days=10
        )

    i = 0
    while i < len(pending):
        code = pending[i]
        if i > 0:
            gap = random.uniform(interval_lo, interval_hi)
            print(f"{_LOG} 【逐票等待】{gap:.0f}s 后拉 {code}", flush=True)
            time.sleep(gap)
        try:
            recs = asyncio.run(_pull(code))
            net = _sum_main_net_5d(recs)
        except Exception as exc:  # noqa: BLE001
            if _is_disconnect(exc):
                sec = random.uniform(pause_lo, pause_hi)
                print(
                    f"{_LOG} 【逐票断连】{code} · {type(exc).__name__}: {exc}",
                    flush=True,
                )
                print(
                    f"{_LOG} 【休眠开始】约 {sec / 60:.1f} 分钟（{sec:.0f}s）· "
                    f"不记完成 · 醒来后重试 {code}",
                    flush=True,
                )
                time.sleep(sec)
                print(f"{_LOG} 【休眠结束】重试 {code}", flush=True)
                continue  # 同码再试，断点不前进
            print(
                f"{_LOG} 【逐票失败】{i + 1}/{len(pending)} {code} · "
                f"{type(exc).__name__}: {exc}（记完成，不再重打）",
                flush=True,
            )
            fail_n += 1
            done.add(code)
            _write_per_symbol_state(as_of, done, filled)
            i += 1
            continue

        done.add(code)
        if net is not None:
            filled[code] = float(net)
            ok_n += 1
            print(
                f"{_LOG} 【逐票成功】{i + 1}/{len(pending)} {code} net={net:.0f}",
                flush=True,
            )
        else:
            fail_n += 1
            print(
                f"{_LOG} 【逐票空数据】{i + 1}/{len(pending)} {code}（记完成，不再重打）",
                flush=True,
            )
        _write_per_symbol_state(as_of, done, filled)
        i += 1

    return ok_n, fail_n, filled
