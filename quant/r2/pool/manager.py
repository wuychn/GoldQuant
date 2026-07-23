"""双池状态机。"""

from __future__ import annotations

from quant.r2.config import load_r2_config
from quant.r2.domain.models import PoolMember, PoolStage, RegimeSnapshot, SectorRow
from quant.r2.io import state as state_io
from quant.r2.pool.universe import scan_universe
from quant.r2.signals.structure import structure_score
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
    min_struct = float(cfg.get("promote_min_structure_score", 65))
    today = date_str or cn_date_str()

    eligible = {s.name for s in sectors if s.eligible}
    faded = {s.name for s in sectors if s.lifecycle.value == "退潮" or not s.eligible}

    tracking = _index(state_io.load_tracking())
    combat = _index(state_io.load_combat())

    candidates = scan_universe(payload, eligible)
    added_t = 0
    for row in candidates:
        code = str(row.get("股票代码") or "").strip()
        if not code or code in combat:
            continue
        sc = structure_score(row)
        if sc < 55:
            continue
        if code not in tracking:
            tracking[code] = PoolMember(
                code=code,
                name=str(row.get("股票名称") or "").strip(),
                stage=PoolStage.TRACKING,
                structure_score=sc,
                sector_tags=list(row.get("sector_tags") or []),
                first_seen=today,
                reason=f"板块{','.join(row.get('sector_tags') or [])}",
                snapshot=dict(row),
            )
            added_t += 1
        else:
            tracking[code].structure_score = max(tracking[code].structure_score, sc)

    promoted = 0
    if regime.allow_new_combat:
        cap = _cap(regime)
        for code, mem in sorted(tracking.items(), key=lambda x: -x[1].structure_score):
            if len(combat) >= cap:
                break
            if mem.structure_score < min_struct:
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

    tracking_list = sorted(tracking.values(), key=lambda m: -m.structure_score)[:tracking_max]
    combat_list = sorted(combat.values(), key=lambda m: -m.structure_score)[: _cap(regime)]

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
