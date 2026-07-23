"""R2 各时段 phase 处理。"""

from __future__ import annotations

from quant.data_fetch import fetch_mode
from quant.execution.executor import ExecutedTrade, execute_signals
from quant.progress_log import log_progress, log_progress_done
from quant.r2.io import state as state_io
from quant.r2.io.state import inject_combat_watchlist
from quant.r2.market.regime import detect_regime
from quant.r2.pool.manager import run_evening_pool_update
from quant.r2.sector.engine import build_sector_rows
from quant.r2.signals.pipeline import generate_confirmed_signals
from quant.scoring.context import ScoreContext
from quant.scoring.theme_tracker import update_concept_tracker_state
from quant.store.snapshot import save_derived, save_raw
from quant.store.state import merge_payload_holdings
from quant.timeutil import cn_date_str


def _prepare_payload(raw: dict) -> dict:
    from quant.data_fetch import unwrap_payload

    payload = unwrap_payload(raw)
    return merge_payload_holdings(payload)


def _inject_combat(payload: dict) -> dict:
    return inject_combat_watchlist(payload, date_str=cn_date_str())


def run_pre_market(raw: dict, *, timestamp: str) -> dict:
    scope = "pre_market"
    log_progress(scope, "R2 盘前")
    payload = _prepare_payload(raw)
    regime = detect_regime(payload)
    state_io.save_regime(
        {
            "regime": regime.regime.value,
            "zt_count": regime.zt_count,
            "combat_cap": regime.combat_cap,
        }
    )
    save_raw("pre_market", payload)
    log_progress_done(scope, "R2 盘前完成", detail=regime.regime.value)
    return {"regime": regime.regime.value, "payload": payload}


def run_during_market(raw: dict, *, timestamp: str) -> dict:
    scope = "during_market"
    log_progress(scope, "R2 盘中")
    payload = _inject_combat(_prepare_payload(raw))
    ctx = ScoreContext.from_payload(payload, mode="during_market")
    raw_buy, raw_sell, executable, audit, regime = generate_confirmed_signals(
        ctx, mode="during_market"
    )
    executed, _ = execute_signals(executable, payload=payload)

    shadow = []
    for sig in raw_buy:
        shadow.append({"code": sig.code, "action": "buy", "reason": sig.reason})
    from quant.r2.ml.runtime import write_shadow_scores

    write_shadow_scores(shadow, date_str=cn_date_str())

    save_derived(
        "r2_signals.json",
        {
            "raw_buy": len(raw_buy),
            "raw_sell": len(raw_sell),
            "executed": len(executed),
            "regime": regime.regime.value,
            "audit": audit[:20],
        },
    )
    save_raw("during_market", payload)
    log_progress_done(scope, "R2 盘中完成", detail=f"成交 {len(executed)}")
    return {
        "executed": executed,
        "regime": regime.regime.value,
        "payload": payload,
    }


def run_evening(raw: dict, *, timestamp: str) -> dict:
    scope = "post_market_evening"
    log_progress(scope, "R2 晚间复盘")
    payload = _prepare_payload(raw)
    update_concept_tracker_state(payload)
    regime = detect_regime(payload)
    sectors = build_sector_rows(payload)
    state_io.save_sector_snapshot(sectors)
    state_io.save_regime(
        {
            "regime": regime.regime.value,
            "zt_count": regime.zt_count,
            "combat_cap": regime.combat_cap,
        }
    )
    tracking, combat, stats = run_evening_pool_update(
        payload, regime=regime, sectors=sectors, date_str=cn_date_str()
    )
    save_derived("r2_pool_stats.json", stats)
    save_raw("post_market_evening", payload)
    log_progress_done(
        scope,
        "R2 晚间完成",
        detail=f"tracking={stats['tracking']} combat={stats['combat']}",
    )
    return {"stats": stats, "regime": regime.regime.value, "payload": payload}


def run_lunch(raw: dict, *, timestamp: str) -> dict:
    payload = _inject_combat(_prepare_payload(raw))
    regime = detect_regime(payload)
    save_raw("post_market_lunch", payload)
    return {"regime": regime.regime.value, "payload": payload}
