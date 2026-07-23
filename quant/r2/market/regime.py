"""市场环境档位。"""

from __future__ import annotations

from quant.r2.config import load_r2_config
from quant.r2.domain.models import Regime, RegimeSnapshot
from quant.r2.io.payload import market_snapshot


def detect_regime(payload: dict) -> RegimeSnapshot:
    cfg = load_r2_config().get("regime") or {}
    snap = market_snapshot(payload)
    zt = int(snap["zt_count"])
    up = int(snap["up_count"])
    down = int(snap["down_count"])
    idx = snap.get("index_chg")
    height = int(snap.get("zt_height") or 0)

    strong_zt = int(cfg.get("strong_zt_min", 60))
    weak_zt = int(cfg.get("weak_zt_max", 29))
    strong_idx = float(cfg.get("strong_index_chg", 0.5))

    votes_s = votes_w = 0
    if idx is not None:
        if idx > strong_idx:
            votes_s += 1
        elif idx < -strong_idx:
            votes_w += 1
    if up > down:
        votes_s += 1
    elif up < down:
        votes_w += 1
    if zt >= strong_zt:
        votes_s += 1
    elif zt <= weak_zt:
        votes_w += 1
    if height >= 5:
        votes_s += 1
    elif height <= 2:
        votes_w += 1

    if votes_s >= 3 and zt >= 50:
        regime = Regime.STRONG
    elif votes_w >= 3:
        regime = Regime.WEAK
    else:
        regime = Regime.NEUTRAL

    pool_cfg = load_r2_config().get("pool") or {}
    cap_map = {
        Regime.STRONG: int(pool_cfg.get("combat_max_strong", 12)),
        Regime.NEUTRAL: int(pool_cfg.get("combat_max_neutral", 10)),
        Regime.WEAK: int(pool_cfg.get("combat_max_weak", 0)),
    }
    cap = cap_map[regime]
    allow_new = regime != Regime.WEAK

    return RegimeSnapshot(
        regime=regime,
        zt_count=zt,
        up_count=up,
        down_count=down,
        index_chg=idx if isinstance(idx, (int, float)) else None,
        combat_cap=cap,
        allow_new_combat=allow_new,
        note=f"votes_s={votes_s} votes_w={votes_w}",
    )
