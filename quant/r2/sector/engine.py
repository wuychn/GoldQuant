"""板块主线：生命周期 + 防御过滤。"""

from __future__ import annotations

from quant.r2.config import load_r2_config
from quant.r2.domain.models import Lifecycle, SectorRow
from quant.r2.io.payload import market_snapshot
from quant.r2.sector.defensive import is_defensive_sector
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY, section_board_rows


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


def _days_on_board(name: str, history: list[list[dict]]) -> int:
    days = 0
    for snap in reversed(history[-10:]):
        names = {r.get("name") for r in snap if isinstance(r, dict)}
        if name in names:
            days += 1
        else:
            break
    return days


def _lifecycle(
    *,
    days: int,
    change_pct: float | None,
    rank_gain: int | None,
    defensive: bool,
    index_chg: float | None,
) -> Lifecycle:
    if defensive and index_chg is not None and index_chg < 0 and (change_pct or 0) > 0:
        return Lifecycle.FADE
    if days <= 2:
        return Lifecycle.SPROUT
    if (change_pct or 0) >= 5 and (rank_gain or 99) <= 3:
        return Lifecycle.CLIMAX
    if days >= 3:
        return Lifecycle.SPREAD
    return Lifecycle.SPROUT


def build_sector_rows(
    payload: dict,
    *,
    sector_history: list[list[dict]] | None = None,
) -> list[SectorRow]:
    cfg = load_r2_config().get("sector") or {}
    top_n = int(cfg.get("board_top_n", 10))
    gain_c = section_board_rows(payload, BOARD_CONCEPT, "涨幅榜", limit=top_n)
    fund_c = section_board_rows(payload, BOARD_CONCEPT, "资金流入榜", limit=top_n)
    gain_i = section_board_rows(payload, BOARD_INDUSTRY, "涨幅榜", limit=top_n)

    rank_gain = _rank_map(gain_c)
    rank_fund = _rank_map(fund_c)
    names = set(rank_gain) | set(rank_fund)

    ms = market_snapshot(payload)
    idx_chg = ms.get("index_chg")
    hist = sector_history or []
    rows: list[SectorRow] = []

    for name in names:
        gain_row = next((r for r in gain_c if _board_name(r) == name), {})
        chg = _f(gain_row, "行业-涨跌幅", "涨跌幅")
        net = _f(gain_row, "净额", "净流入")
        days = _days_on_board(name, hist) + (1 if name in rank_gain else 0)
        defensive = is_defensive_sector(name)
        lc = _lifecycle(
            days=days,
            change_pct=chg,
            rank_gain=rank_gain.get(name),
            defensive=defensive,
            index_chg=idx_chg if isinstance(idx_chg, (int, float)) else None,
        )
        eligible = lc in (Lifecycle.SPROUT, Lifecycle.SPREAD)
        note = ""
        if defensive:
            note = "防御板块"
            eligible = False
        if lc == Lifecycle.CLIMAX:
            note = (note + ";高潮禁新进").strip(";")
            eligible = False

        rows.append(
            SectorRow(
                name=name,
                section="概念板块",
                rank_gain=rank_gain.get(name),
                rank_fund=rank_fund.get(name),
                change_pct=chg,
                net_flow=net,
                days_on_gain_board=days,
                lifecycle=lc,
                defensive=defensive,
                eligible=eligible,
                note=note,
            )
        )

    for row in gain_i:
        name = _board_name(row)
        if not name or name in names:
            continue
        chg = _f(row, "行业-涨跌幅", "涨跌幅")
        net = _f(row, "净额", "净流入")
        defensive = is_defensive_sector(name)
        lc = _lifecycle(
            days=1,
            change_pct=chg,
            rank_gain=None,
            defensive=defensive,
            index_chg=idx_chg if isinstance(idx_chg, (int, float)) else None,
        )
        eligible = lc in (Lifecycle.SPROUT, Lifecycle.SPREAD) and not defensive
        rows.append(
            SectorRow(
                name=name,
                section="行业板块",
                rank_gain=None,
                rank_fund=None,
                change_pct=chg,
                net_flow=net,
                days_on_gain_board=1,
                lifecycle=lc,
                defensive=defensive,
                eligible=eligible,
                note="行业榜",
            )
        )
    return rows


def eligible_sector_names(rows: list[SectorRow]) -> set[str]:
    return {r.name for r in rows if r.eligible}
