"""板块主线：多因子 + 广度批量缓存 + 资金动量。"""

from __future__ import annotations

from quant.config import load_r2_config
from quant.domain.models import SectorRow
from quant.factors.breadth_cache import enrich_row_breadth, prefetch_batch
from quant.factors.fund_momentum import enrich_fund_momentum, fund_momentum_score
from quant.factors.history import append_sector_snapshot, days_on_gain_board, load_sector_history
from quant.factors.sector import score_sector_row
from quant.io.payload import market_snapshot
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY, section_board_rows
from quant.sector.defensive import is_defensive_sector
from quant.timeutil import cn_date_str


def _board_name(row: dict) -> str:
    return str(row.get("行业") or row.get("板块") or "").strip()


def _f(row: dict, *keys: str) -> float | None:
    for k in keys:
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _rank_map(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, row in enumerate(rows, start=1):
        name = _board_name(row)
        if name:
            out[name] = i
    return out


def _int_or_none(v: object) -> int | None:
    try:
        return int(float(v)) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def build_sector_rows(
    payload: dict,
    *,
    sector_history: list[dict] | None = None,
    date_str: str | None = None,
    use_ak_breadth: bool | None = None,
    persist_history: bool = True,
    memory_history: list[dict] | None = None,
    allow_live_ak: bool | None = None,
) -> list[SectorRow]:
    cfg = load_r2_config().get("sector") or {}
    top_n = int(cfg.get("board_top_n", 10))
    use_ak = bool(cfg.get("use_ak_breadth", True)) if use_ak_breadth is None else use_ak_breadth
    ds = date_str or cn_date_str()

    gain_c = section_board_rows(payload, BOARD_CONCEPT, "涨幅榜", limit=top_n)
    fund_c = section_board_rows(payload, BOARD_CONCEPT, "资金流入榜", limit=top_n)
    gain_i = section_board_rows(payload, BOARD_INDUSTRY, "涨幅榜", limit=top_n)

    rank_gain = _rank_map(gain_c)
    rank_fund = _rank_map(fund_c)
    names = set(rank_gain) | set(rank_fund)

    ms = market_snapshot(payload)
    idx_chg = ms.get("index_chg")
    hist = sector_history if sector_history is not None else load_sector_history()

    prefetch_items: list[tuple[str, str, dict]] = []
    for name in names:
        gain_row = next((r for r in gain_c if _board_name(r) == name), {})
        prefetch_items.append((name, BOARD_CONCEPT, dict(gain_row)))
    for row in gain_i:
        n = _board_name(row)
        if n and n not in names:
            prefetch_items.append((n, BOARD_INDUSTRY, dict(row)))

    if use_ak and prefetch_items:
        prefetch_batch(prefetch_items, date_str=ds, allow_live_ak=allow_live_ak)

    rows: list[SectorRow] = []
    snapshot_payload: list[dict] = []

    for name in names:
        gain_row = next((r for r in gain_c if _board_name(r) == name), {})
        if use_ak:
            gain_row = enrich_row_breadth(
                dict(gain_row),
                name=name,
                section=BOARD_CONCEPT,
                date_str=ds,
                allow_live_ak=allow_live_ak,
            )
            gain_row = enrich_fund_momentum(gain_row, name=name, section=BOARD_CONCEPT, date_str=ds)
        chg = _f(gain_row, "行业-涨跌幅", "涨跌幅")
        net = _f(gain_row, "净额", "净流入")
        on_board = name in rank_gain
        days = days_on_gain_board(name, hist, on_board_today=on_board)
        defensive = is_defensive_sector(name)
        fmom = fund_momentum_score(name, BOARD_CONCEPT, date_str=ds)

        fac = score_sector_row(
            name=name,
            gain_row=gain_row,
            rank_gain=rank_gain.get(name),
            rank_fund=rank_fund.get(name),
            persistence_days=days,
            index_chg=idx_chg if isinstance(idx_chg, (int, float)) else None,
            fund_momentum=fmom,
            top_n=top_n,
        )

        rows.append(
            SectorRow(
                name=name,
                section=BOARD_CONCEPT,
                rank_gain=rank_gain.get(name),
                rank_fund=rank_fund.get(name),
                change_pct=chg,
                net_flow=net,
                days_on_gain_board=days,
                lifecycle=fac.lifecycle,
                defensive=defensive,
                eligible=fac.eligible and fac.new_entry,
                note=fac.note,
                factor_score=fac.composite,
                rs_vs_index=fac.rs_vs_index,
                fund_score=fac.fund_score,
                breadth_score=fac.breadth_score,
                crowd_penalty=fac.crowd_penalty,
                up_count=_int_or_none(gain_row.get("上涨家数")),
                down_count=_int_or_none(gain_row.get("下跌家数")),
            )
        )
        if on_board:
            snapshot_payload.append({"name": name, "rank_gain": rank_gain.get(name)})

    for row in gain_i:
        name = _board_name(row)
        if not name or name in names:
            continue
        enriched = dict(row)
        if use_ak:
            enriched = enrich_row_breadth(
                enriched,
                name=name,
                section=BOARD_INDUSTRY,
                date_str=ds,
                allow_live_ak=allow_live_ak,
            )
            enriched = enrich_fund_momentum(enriched, name=name, section=BOARD_INDUSTRY, date_str=ds)
        chg = _f(enriched, "行业-涨跌幅", "涨跌幅")
        net = _f(enriched, "净额", "净流入")
        defensive = is_defensive_sector(name)
        days = days_on_gain_board(name, hist, on_board_today=True)
        fmom = fund_momentum_score(name, BOARD_INDUSTRY, date_str=ds)

        fac = score_sector_row(
            name=name,
            gain_row=enriched,
            rank_gain=None,
            rank_fund=None,
            persistence_days=days,
            index_chg=idx_chg if isinstance(idx_chg, (int, float)) else None,
            fund_momentum=fmom,
            top_n=top_n,
        )

        rows.append(
            SectorRow(
                name=name,
                section=BOARD_INDUSTRY,
                rank_gain=None,
                rank_fund=None,
                change_pct=chg,
                net_flow=net,
                days_on_gain_board=days,
                lifecycle=fac.lifecycle,
                defensive=defensive,
                eligible=fac.eligible and fac.new_entry,
                note=(fac.note + ";行业榜").strip(";"),
                factor_score=fac.composite,
                rs_vs_index=fac.rs_vs_index,
                fund_score=fac.fund_score,
                breadth_score=fac.breadth_score,
                crowd_penalty=fac.crowd_penalty,
                up_count=_int_or_none(enriched.get("上涨家数")),
                down_count=_int_or_none(enriched.get("下跌家数")),
            )
        )
        snapshot_payload.append({"name": name, "rank_gain": None})

    if ds and snapshot_payload:
        append_sector_snapshot(
            ds,
            snapshot_payload,
            persist=persist_history,
            memory_history=memory_history,
        )

    return rows


def eligible_sector_names(rows: list[SectorRow]) -> set[str]:
    return {r.name for r in rows if r.eligible}
