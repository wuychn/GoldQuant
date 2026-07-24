"""双池状态机（板块因子 eligible + 个股 alpha）。"""

from __future__ import annotations

from quant.config import load_r2_config
from quant.domain.models import PoolMember, PoolStage, RegimeSnapshot, SectorRow
from quant.factors.stock import compute_stock_alpha
from quant.io import state as state_io
from quant.io.payload import market_snapshot
from quant.pool.universe import scan_universe
from quant.scoring.context import ScoreContext
from quant.timeutil import cn_date_str


def _cap(regime: RegimeSnapshot) -> int:
    return max(0, int(regime.combat_cap))


def _index(members: list[PoolMember]) -> dict[str, PoolMember]:
    return {m.code: m for m in members}


def run_evening_pool_update(
    payload: dict,
    *,
    regime: RegimeSnapshot,
    sectors: list[SectorRow],
    date_str: str | None = None,
) -> tuple[list[PoolMember], list[PoolMember], dict]:
    """返回 (tracking, combat, stats)。"""
    cfg = load_r2_config().get("pool") or {}
    tracking_max = int(cfg.get("tracking_max", 50))
    min_days = int(cfg.get("tracking_min_days_before_combat", 1))
    min_alpha_track = float(cfg.get("tracking_min_alpha", cfg.get("tracking_min_structure_score", 55)))
    min_alpha_combat = float(cfg.get("promote_min_alpha", cfg.get("promote_min_structure_score", 65)))
    today = date_str or cn_date_str()

    eligible = {s.name for s in sectors if s.eligible}
    faded = {
        s.name
        for s in sectors
        if s.lifecycle.value == "退潮" or (not s.eligible and s.factor_score < 45)
    }

    sector_changes = {s.name: float(s.change_pct or 0) for s in sectors if s.change_pct is not None}
    ms = market_snapshot(payload)
    idx_chg = ms.get("index_chg")
    ctx = ScoreContext.from_payload(payload, mode="post_market_evening")

    tracking = _index(state_io.load_tracking())
    combat = _index(state_io.load_combat())

    candidates = scan_universe(payload, eligible)
    added_t = 0
    for row in candidates:
        code = str(row.get("股票代码") or "").strip()
        if not code or code in combat:
            continue
        tags = list(row.get("sector_tags") or [])
        alpha = compute_stock_alpha(
            row,
            ctx=ctx,
            index_chg=idx_chg if isinstance(idx_chg, (int, float)) else None,
            sector_tags=tags,
            sector_changes=sector_changes,
        )
        score = alpha["alpha_score"]
        if score < min_alpha_track:
            continue
        if code not in tracking:
            top_sector = tags[0] if tags else ""
            sec = next((s for s in sectors if s.name == top_sector), None)
            fac_note = f"板块因子{sec.factor_score:.0f}" if sec else ""
            tracking[code] = PoolMember(
                code=code,
                name=str(row.get("股票名称") or "").strip(),
                stage=PoolStage.TRACKING,
                structure_score=alpha["structure_score"],
                alpha_score=score,
                sector_tags=tags,
                first_seen=today,
                reason=f"{fac_note};alpha={score:.0f};{','.join(tags)}",
                snapshot=dict(row),
            )
            added_t += 1
        else:
            tracking[code].structure_score = max(tracking[code].structure_score, alpha["structure_score"])
            tracking[code].alpha_score = max(tracking[code].alpha_score, score)

    promoted = 0
    if regime.allow_new_combat:
        cap = _cap(regime)
        for code, mem in sorted(tracking.items(), key=lambda x: -x[1].alpha_score):
            if len(combat) >= cap:
                break
            if mem.alpha_score < min_alpha_combat:
                continue
            if mem.first_seen and today <= mem.first_seen and min_days > 0:
                continue
            if any(t in faded for t in mem.sector_tags):
                continue
            combat[code] = PoolMember(
                code=mem.code,
                name=mem.name,
                stage=PoolStage.COMBAT,
                structure_score=mem.structure_score,
                alpha_score=mem.alpha_score,
                sector_tags=mem.sector_tags,
                first_seen=mem.first_seen,
                promoted_at=today,
                reason="跟踪池升档",
                snapshot=dict(mem.snapshot or {}),
            )
            tracking.pop(code, None)
            promoted += 1

    removed_c = 0
    for code in list(combat.keys()):
        mem = combat[code]
        if any(t in faded for t in mem.sector_tags) and mem.code not in {
            str(r.get("股票代码", "")).strip()
            for r in payload.get("持仓股") or []
            if isinstance(r, dict)
        }:
            del combat[code]
            removed_c += 1

    tracking_list = sorted(tracking.values(), key=lambda m: -m.alpha_score)[:tracking_max]
    combat_list = sorted(combat.values(), key=lambda m: -m.alpha_score)[: _cap(regime)]

    state_io.save_tracking(tracking_list)
    state_io.save_combat(combat_list)

    stats = {
        "tracking": len(tracking_list),
        "combat": len(combat_list),
        "added_tracking": added_t,
        "promoted_combat": promoted,
        "removed_combat": removed_c,
    }
    return tracking_list, combat_list, stats
