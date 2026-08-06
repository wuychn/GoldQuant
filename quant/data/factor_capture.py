"""PIT 因子快照采集：hot_rank / fund_flow / theme_mom。

所有 facade 返回统一 schema——业务层不解析源差异：
- fetch_em_hot_rank → DataFrame(code, rank)
- fetch_spot → DataFrame(code, name, open, high, low, close, pre_close, volume, amount, turnover_rate, float_mv, total_mv)
- fetch_concept_boards → DataFrame(板块名称, 涨跌幅)
- fetch_stock_fund_flow_daily → list[dict]，每条含 'main_net_inflow'(元)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from quant.data.factor_snapshots import (
    write_fund_flow_snapshot,
    write_hot_rank_snapshot,
    write_theme_snapshot,
)

logger = logging.getLogger(__name__)


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
        print(f"[WARN] hot_rank 拉取失败: {e}", file=sys.stderr if False else None) or logger.warning("hot_rank: %s", e)
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


def capture_fund_flow(as_of: str, spot: pd.DataFrame, universe_codes: list[str] | None = None) -> int:
    """主力 5 日净流入 / 流通市值（universe 优先，限流保护）。

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
    targets = universe_codes or list(mv_map.keys())
    # flow_ratio_5 必须取 5 日累加；spot 的 main_net_inflow 是当日值，不可混用
    need_fetch = [c for c in dict.fromkeys(targets) if mv_map.get(c)]
    extra_flows = _fetch_flows_5d_batch(need_fetch)
    rows: list[dict] = []
    for code in targets:
        mv = mv_map.get(code)
        if not mv or mv <= 0:
            continue
        net = extra_flows.get(code)
        if net is None:
            continue
        ratio = net / mv
        if abs(ratio) < 1e8:
            rows.append({"code": code, "flow_ratio_5": round(ratio, 6)})
    if rows:
        write_fund_flow_snapshot(as_of, rows)
    return len(rows)


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


def _fetch_flows_5d_batch(codes: list[str]) -> dict[str, float]:
    """批量取 5 日主力净流入：一次 asyncio.run 并发拉取，避免 per-call 起事件循环。

    走 try_with_fallback_async，fallback 顺序由 yml 决定。
    """
    if not codes:
        return {}
    try:
        import asyncio

        from quant.data.sources.interface import try_with_fallback_async

        async def _gather() -> list:
            return await asyncio.gather(
                *[try_with_fallback_async("enrich", "fetch_stock_fund_flow_daily", symbol=c, days=10) for c in codes]
            )

        recs_list = asyncio.run(_gather())
    except Exception:
        return {}
    out: dict[str, float] = {}
    for code, recs in zip(codes, recs_list or []):
        v = _sum_flow_5d(recs)
        if v is not None:
            out[code] = v
    return out


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


def capture_all_factor_snapshots(
    as_of: str, spot: pd.DataFrame | None = None, universe_codes: list[str] | None = None
) -> dict[str, int]:
    """一次性采集 hot/flow/theme 快照（基本面统一走 fundamental_pit）。"""
    if spot is None:
        try:
            from quant.data.fetch import fetch_spot_em

            spot = fetch_spot_em()
        except Exception:
            spot = pd.DataFrame()
    n_hot = capture_hot_rank(as_of)
    n_flow = capture_fund_flow(as_of, spot if isinstance(spot, pd.DataFrame) else pd.DataFrame(), universe_codes)
    n_theme = capture_theme_mom(as_of, spot if isinstance(spot, pd.DataFrame) else None)
    return {"hot": n_hot, "flow": n_flow, "theme": n_theme}
