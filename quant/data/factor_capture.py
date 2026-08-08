"""PIT 因子快照采集：hot_rank / fund_flow / theme_mom。

所有 facade 返回统一 schema——业务层不解析源差异：
- fetch_em_hot_rank → DataFrame(code, rank)
- fetch_spot → DataFrame(code, name, open, high, low, close, pre_close, volume, amount, turnover_rate, float_mv, total_mv)
- fetch_concept_boards → DataFrame(板块名称, 涨跌幅)
- fetch_stock_fund_flow_daily → list[dict]，每条含 'main_net_inflow'(元)

fund_flow 支持分批落盘断点续传：每批 upsert 快照 + 记录已尝试码，
重跑跳过已成功/已确认无数据的码；网络失败不记完成，下次重试。
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from quant.data.calendar import to_iso
from quant.data.factor_snapshots import (
    read_fund_flow_snapshot,
    upsert_fund_flow_snapshot,
    write_hot_rank_snapshot,
    write_theme_snapshot,
)
from quant.store.paths import quant_home

logger = logging.getLogger(__name__)

_DEFAULT_FLOW_FLUSH_EVERY = 50


def _num(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def capture_hot_rank(as_of: str) -> int:
    """东财人气榜 → 截面 rank z-score。

    facade 返回 DataFrame(code, rank)，业务层直接用，不解析列名。
    """
    try:
        from quant.data.sources.interface import try_with_fallback

        df = try_with_fallback("market", "fetch_em_hot_rank")
    except Exception as e:
        logger.warning("hot_rank: %s", e)
        return 0
    if df is None or df.empty:
        return 0
    rows_raw: list[tuple[str, float]] = []
    for _, r in df.iterrows():
        code = str(r.get("code", "")).strip()
        if not code:
            continue
        rank = _num(r.get("rank"))
        if rank is None:
            continue
        rows_raw.append((code, rank))
    if len(rows_raw) < 5:
        return 0
    ranks = np.array([x[1] for x in rows_raw], dtype=float)
    mu, sd = float(ranks.mean()), float(ranks.std(ddof=1) or 1.0)
    rows = [{"code": c, "hot_rank_z": round(-(rk - mu) / max(sd, 1e-6), 4)} for c, rk in rows_raw]
    write_hot_rank_snapshot(as_of, rows)
    return len(rows)


def _flow_progress_path(as_of: str) -> Path:
    return quant_home() / "data" / "fund_flow_progress" / f"{to_iso(as_of)}.json"


def _read_flow_attempted(as_of: str) -> set[str]:
    p = _flow_progress_path(as_of)
    if not p.is_file():
        return set()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    codes = raw.get("attempted") or []
    return {str(c).strip() for c in codes if str(c).strip()}


def _write_flow_attempted(as_of: str, attempted: set[str]) -> None:
    p = _flow_progress_path(as_of)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": to_iso(as_of),
        "attempted": sorted(attempted),
        "n": len(attempted),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def capture_fund_flow(
    as_of: str,
    spot: pd.DataFrame,
    universe_codes: list[str] | None = None,
    *,
    flush_every: int = _DEFAULT_FLOW_FLUSH_EVERY,
) -> int:
    """主力 5 日净流入 / 流通市值（universe 优先；分批落盘可断点续传）。

    spot 由 facade 统一返回 DataFrame(code, float_mv, ...)，业务层直接用。
    """
    if spot is None or spot.empty:
        return 0
    mv_map: dict[str, float] = {}
    for _, r in spot.iterrows():
        code = str(r.get("code", "")).strip()
        mv = _num(r.get("float_mv"))
        if code and mv and mv > 0:
            mv_map[code] = mv
    targets = [c for c in dict.fromkeys(universe_codes or list(mv_map.keys())) if mv_map.get(c)]
    if not targets:
        return 0

    # 已落盘成功码 + 已尝试（含无 5 日数据）→ 跳过；网络失败不进 attempted
    done = set(read_fund_flow_snapshot(as_of, exact=True)) | _read_flow_attempted(as_of)
    pending = [c for c in targets if c not in done]
    flush_every = max(1, int(flush_every))
    n_done = len(set(targets) & done)
    print(
        f"fund_flow: 目标 {len(targets)} · 已跳过 {n_done} · 待拉 {len(pending)} "
        f"（flush_every={flush_every}）",
        flush=True,
    )
    if not pending:
        return len(read_fund_flow_snapshot(as_of, exact=True))

    attempted = _read_flow_attempted(as_of)
    total_ok = 0
    n_err = 0
    for i in range(0, len(pending), flush_every):
        batch = pending[i : i + flush_every]
        flows, batch_ok, batch_err = _fetch_flows_5d_batch(batch)
        rows: list[dict] = []
        for code in batch:
            if code in batch_err:
                n_err += 1
                continue
            # 成功返回（含无数据）→ 记 attempted，避免重跑再打
            attempted.add(code)
            net = flows.get(code)
            if net is None:
                continue
            mv = mv_map.get(code)
            if not mv or mv <= 0:
                continue
            ratio = net / mv
            if abs(ratio) < 1e8:
                rows.append({"code": code, "flow_ratio_5": round(ratio, 6)})
        if rows:
            upsert_fund_flow_snapshot(as_of, rows)
            total_ok += len(rows)
        _write_flow_attempted(as_of, attempted)
        done_n = min(i + len(batch), len(pending))
        n_empty = len(batch_ok) - len(flows)
        print(
            f"fund_flow 进度 {done_n}/{len(pending)} "
            f"（本批 ok={len(rows)} empty={n_empty} err={len(batch_err)} · 累计落盘+{total_ok}）",
            flush=True,
        )

    if n_err:
        print(
            f"[WARN] fund_flow: {n_err} 只网络/源失败未记完成，重跑将重试",
            file=sys.stderr,
            flush=True,
        )
    return len(read_fund_flow_snapshot(as_of, exact=True))


def _sum_flow_5d(recs: list) -> float | None:
    """资金流日线近 5 根主力净流入合计（元）；不足 5 根或全 0 返回 None。

    facade 统一返回 list[dict]，每条含 'main_net_inflow'(元)。
    """
    recs = recs or []
    if len(recs) < 5:
        return None
    total = 0.0
    for r in recs[-5:]:
        v = r.get("main_net_inflow")
        if v is not None:
            yuan = _num(v)
            if yuan is not None:
                total += yuan
    return total if total != 0 else None


def _flow_concurrency() -> int:
    from quant.config import load_quant_config

    data = load_quant_config().get("data") or {}
    n = data.get("eastmoney_max_concurrent", data.get("default_max_concurrent", 1))
    return max(1, int(n))


def _fetch_flows_5d_batch(
    codes: list[str],
) -> tuple[dict[str, float], set[str], set[str]]:
    """拉取一批 5 日主力净流入。

    返回 ``(code→净流入, 成功集合含空数据, 失败集合)``。
    失败不写入 progress，便于断点重试。
    """
    if not codes:
        return {}, set(), set()
    try:
        import asyncio

        from quant.data.sources.interface import try_with_fallback_async

        sem = asyncio.Semaphore(_flow_concurrency())

        async def _one(code: str) -> tuple[str, str, float | None]:
            """status: ok | err"""
            async with sem:
                try:
                    recs = await try_with_fallback_async(
                        "enrich", "fetch_stock_fund_flow_daily", symbol=code, days=10
                    )
                    return code, "ok", _sum_flow_5d(recs)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("fund_flow %s 失败: %s", code, exc)
                    return code, "err", None

        async def _gather() -> list:
            return await asyncio.gather(*[_one(c) for c in codes])

        results = asyncio.run(_gather())
    except Exception as exc:  # noqa: BLE001
        logger.warning("fund_flow batch 失败: %s", exc)
        return {}, set(), set(codes)

    out: dict[str, float] = {}
    ok: set[str] = set()
    err: set[str] = set()
    for code, status, v in results or []:
        if status == "err":
            err.add(code)
            continue
        ok.add(code)
        if v is not None:
            out[code] = v
    return out, ok, err


def capture_theme_mom(as_of: str, spot: pd.DataFrame | None = None) -> int:
    """概念板块 5 日涨幅分位 → 个股 theme_mom（PIT 当日板块榜）。

    facade 返回 DataFrame(板块名称, 涨跌幅)，业务层直接用。
    fetch_concept_boards 是 async 接口 → 用 try_with_fallback_async + asyncio.run。
    """
    try:
        import asyncio
        from quant.data.sources.interface import try_with_fallback_async

        boards = asyncio.run(try_with_fallback_async("market", "fetch_concept_boards"))
        if boards is None or (hasattr(boards, "empty") and boards.empty):
            return 0
        board_pct = {
            str(r["板块名称"]): _num(r["涨跌幅"])
            for _, r in boards.iterrows()
            if _num(r["涨跌幅"]) is not None
        }
        if len(board_pct) < 5:
            return 0
        vals = sorted(board_pct.values())
        n = len(vals)

        def _pctile(v: float) -> float:
            return sum(1 for x in vals if x <= v) / n

        if spot is None or spot.empty:
            from quant.data.fetch import fetch_spot_em

            spot = fetch_spot_em()
        rows: list[dict] = []
        for _, r in spot.iterrows():
            code = str(r.get("code", "")).strip()
            if not code:
                continue
            concepts = _stock_concepts(code)
            if not concepts:
                continue
            pcts = [board_pct[c] for c in concepts if c in board_pct]
            if not pcts:
                continue
            theme = _pctile(float(np.mean(pcts)))
            rows.append({"code": code, "theme_mom": round(theme, 4)})
        if rows:
            write_theme_snapshot(as_of, rows)
        return len(rows)
    except Exception as e:
        print(f"[WARN] theme_mom 失败: {e}", file=sys.stderr)
        return 0


def _stock_concepts(code: str) -> list[str]:
    from quant.services.concept_cache import get_stock_concept_cache

    cache = get_stock_concept_cache()
    concepts, _ = cache.lookup(code)
    return concepts or []


def capture_fund_flow_rank(
    as_of: str,
    spot: pd.DataFrame,
    universe_codes: list[str] | None = None,
    *,
    page_size: int | None = None,
    page_interval: float | str | None = None,
    symbol_interval: str | None = None,
    batch_pause: str | None = None,
    burst_pages: str | None = None,
    force: bool = False,
) -> int:
    """主力 5 日净流入 / 流通市值。

    取数编排在 ``quant.data.fund_flow_5d``（分页→失败回退逐票）；本函数只做
    市值归一与快照落盘。
    """
    if spot is None or spot.empty:
        return 0
    mv_map: dict[str, float] = {}
    for _, r in spot.iterrows():
        code = str(r.get("code", "")).strip()
        mv = _num(r.get("float_mv"))
        if code and mv and mv > 0:
            mv_map[code] = mv
    targets = [
        c for c in dict.fromkeys(universe_codes or list(mv_map.keys())) if mv_map.get(c)
    ]
    if not targets:
        return 0

    if not force:
        existing = read_fund_flow_snapshot(as_of, exact=True)
        hit = len(set(existing) & set(targets))
        if hit >= max(1, int(0.98 * len(targets))):
            print(
                f"fund_flow: 快照已覆盖 {hit}/{len(targets)}，跳过拉取",
                flush=True,
            )
            return len(existing)

    from common.utils.source_headers import parse_interval_range
    from quant.data.fund_flow_5d import fetch_main_net_inflow_5d

    page_lo = page_hi = None
    if page_interval is not None:
        page_lo, page_hi = parse_interval_range(page_interval, default=(61.0, 121.0))
        page_lo = max(10.0, page_lo)
        page_hi = max(page_lo, page_hi)
    sym_lo = sym_hi = None
    if symbol_interval is not None:
        sym_lo, sym_hi = parse_interval_range(symbol_interval, default=(10.0, 20.0))
        sym_lo = max(10.0, sym_lo)
        sym_hi = max(sym_lo, sym_hi)
    pause_lo = pause_hi = None
    if batch_pause is not None:
        pause_lo, pause_hi = parse_interval_range(batch_pause, default=(120.0, 240.0))
    burst_lo = burst_hi = None
    if burst_pages is not None:
        b0, b1 = parse_interval_range(burst_pages, default=(1.0, 3.0))
        burst_lo, burst_hi = int(b0), int(b1)

    result = fetch_main_net_inflow_5d(
        as_of=as_of,
        codes=targets,
        page_size=page_size,
        page_interval_min=page_lo,
        page_interval_max=page_hi,
        symbol_interval_min=sym_lo,
        symbol_interval_max=sym_hi,
        batch_pause_min_sec=pause_lo,
        batch_pause_max_sec=pause_hi,
        burst_pages_min=burst_lo,
        burst_pages_max=burst_hi,
        force=force,
    )
    df = result.df
    if df is None or df.empty:
        print("[WARN] fund_flow: 无净流入数据", file=sys.stderr, flush=True)
        return len(read_fund_flow_snapshot(as_of, exact=True))

    rows: list[dict] = []
    for _, r in df.iterrows():
        code = str(r.get("code", "")).strip()
        net = _num(r.get("main_net_inflow"))
        mv = mv_map.get(code)
        if not code or net is None or not mv or mv <= 0:
            continue
        ratio = net / mv
        if abs(ratio) < 1e8:
            rows.append({"code": code, "flow_ratio_5": round(ratio, 6)})
    if rows:
        upsert_fund_flow_snapshot(as_of, rows)
    print(
        f"fund_flow: 【落盘】{len(rows)} 码 · 源={result.sources_used} · "
        f"分页页数={result.rank_pages_ok}"
        f"{'（分页未完成）' if result.rank_aborted else ''} · "
        f"逐票成功/失败={result.per_symbol_ok}/{result.per_symbol_fail}",
        flush=True,
    )
    return len(read_fund_flow_snapshot(as_of, exact=True))


def capture_all_factor_snapshots(
    as_of: str,
    spot: pd.DataFrame | None = None,
    universe_codes: list[str] | None = None,
    *,
    flush_every: int = _DEFAULT_FLOW_FLUSH_EVERY,
    page_size: int | None = None,
    page_interval: float | str | None = None,
    symbol_interval: str | None = None,
    batch_pause: str | None = None,
    burst_pages: str | None = None,
    fund_flow_mode: str = "rank",
) -> dict[str, int]:
    """一次性采集 hot/flow/theme 快照（基本面统一走 fundamental_pit）。

    ``fund_flow_mode``: ``rank``（默认，分页全市场）| ``per_symbol``（旧逐票路径）。
    """
    if spot is None:
        try:
            from quant.data.fetch import fetch_spot_em

            spot = fetch_spot_em()
        except Exception:
            spot = pd.DataFrame()
    print("因子快照: hot_rank …", flush=True)
    n_hot = capture_hot_rank(as_of)
    print(f"因子快照: hot_rank={n_hot} · fund_flow …", flush=True)
    spot_df = spot if isinstance(spot, pd.DataFrame) else pd.DataFrame()
    mode = (fund_flow_mode or "rank").strip().lower()
    if mode == "per_symbol":
        n_flow = capture_fund_flow(
            as_of, spot_df, universe_codes, flush_every=flush_every
        )
    else:
        n_flow = capture_fund_flow_rank(
            as_of,
            spot_df,
            universe_codes,
            page_size=page_size,
            page_interval=page_interval,
            symbol_interval=symbol_interval,
            batch_pause=batch_pause,
            burst_pages=burst_pages,
        )
    print(f"因子快照: fund_flow={n_flow} · theme_mom …", flush=True)
    n_theme = capture_theme_mom(as_of, spot if isinstance(spot, pd.DataFrame) else None)
    return {"hot": n_hot, "flow": n_flow, "theme": n_theme}
