"""因子 PIT 快照采集：hot / flow / theme（基本面见 fundamental_pit）。"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from quant.data.factor_snapshots import write_fund_flow_snapshot, write_hot_rank_snapshot, write_theme_snapshot


def _num(v: object) -> float | None:
    try:
        f = float(v)
        return f if f == f and abs(f) < 1e12 else None
    except (TypeError, ValueError):
        return None


def capture_hot_rank(as_of: str) -> int:
    """东财人气榜 → 截面 rank z-score。"""
    try:
        from quant.data.sources.interface import try_with_fallback

        df = try_with_fallback("market", "fetch_em_hot_rank")
    except Exception as e:
        print(f"[WARN] hot_rank 拉取失败: {e}", file=sys.stderr)
        return 0
    if df is None or df.empty:
        return 0
    code_col = "代码" if "代码" in df.columns else df.columns[1]
    rank_col = "当前排名" if "当前排名" in df.columns else None
    rows_raw: list[tuple[str, float]] = []
    for i, r in df.iterrows():
        code = str(r.get(code_col, "")).strip()
        if not code:
            continue
        rank = _num(r.get(rank_col)) if rank_col else float(i + 1)
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
    """主力 5 日净流入 / 流通市值（universe 优先，限流保护）。"""
    if spot is None or spot.empty:
        return 0
    code_col = "code" if "code" in spot.columns else "代码"
    mv_col = "float_mv" if "float_mv" in spot.columns else "流通市值"
    mv_map: dict[str, float] = {}
    for _, r in spot.iterrows():
        code = str(r.get(code_col, "")).strip()
        mv = _num(r.get(mv_col))
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
    """资金流日线近 5 根主力净流入合计（元）；不足 5 根或全 0 返回 None。"""
    recs = recs or []
    if len(recs) < 5:
        return None
    from quant.market.fund_flow import amount_to_yuan

    total = 0.0
    for r in recs[-5:]:
        for k in ("主力净流入-净额", "净额", "净流入"):
            v = r.get(k)
            if v is not None:
                yuan = amount_to_yuan(v)
                if yuan is not None:
                    total += yuan
                    break
    return total if total != 0 else None


def _fetch_flows_5d_batch(codes: list[str]) -> dict[str, float]:
    """批量取 5 日主力净流入：一次 ``asyncio.run`` 并发拉取，避免 per-call 起事件循环。

    走 ``EnrichSource.fetch_stock_fund_flow_daily``（经 source，可换源）；东财限流
    (eastmoney_max_concurrent) 内部已串行化。返回 {code: 净流入}（拉取失败/不足的不含）。
    """
    if not codes:
        return {}
    try:
        import asyncio

        from quant.data.sources.factory import get_enrich_source

        src = get_enrich_source()

        async def _gather() -> list:
            return await asyncio.gather(
                *[src.fetch_stock_fund_flow_daily(c, days=10) for c in codes]
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
    """概念板块 5 日涨幅分位 → 个股 theme_mom（PIT 当日板块榜）。"""
    try:
        from quant.data.sources.interface import try_with_fallback

        boards = try_with_fallback("market", "fetch_em_concept_boards")
        if boards is None or boards.empty:
            return 0
        name_col = "板块名称" if "板块名称" in boards.columns else boards.columns[0]
        pct_col = next((c for c in boards.columns if "涨跌幅" in str(c)), None)
        if not pct_col:
            return 0
        board_pct = {
            str(r[name_col]): _num(r[pct_col])
            for _, r in boards.iterrows()
            if _num(r[pct_col]) is not None
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
        code_col = "code" if "code" in spot.columns else "代码"
        rows: list[dict] = []
        for _, r in spot.iterrows():
            code = str(r.get(code_col, "")).strip()
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
        print(f"[WARN] theme_mom 采集失败: {e}", file=sys.stderr)
        return 0


def _stock_concepts(code: str) -> list[str]:
    from quant.services.concept_cache import get_stock_concept_cache

    try:
        cache = get_stock_concept_cache()
        hit, concepts = cache.lookup(code)
        if hit and concepts:
            return concepts
    except Exception:
        pass
    return []


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
