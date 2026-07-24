"""R2 各时段 phase 处理。"""

from __future__ import annotations

from quant.config import load_r2_config
from quant.execution.executor import ExecutedTrade, execute_signals
from quant.progress_log import log_progress, log_progress_done
from quant.io import state as state_io
from quant.io.quotes import build_stock_by_code
from quant.io.state import inject_combat_watchlist
from quant.market.regime import detect_regime
from quant.pool.manager import run_evening_pool_update
from quant.sector.engine import build_sector_rows
from quant.signals.pipeline import generate_confirmed_signals
from quant.scoring.context import ScoreContext
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY
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
    executed, rejected = execute_signals(executable, payload=payload)

    shadow = []
    for sig in raw_buy:
        shadow.append({"code": sig.code, "action": "buy", "reason": sig.reason})
    from quant.ml.runtime import write_shadow_scores

    write_shadow_scores(shadow, date_str=cn_date_str())

    save_derived(
        "signals.json",
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
        "raw_buy": raw_buy,
        "raw_sell": raw_sell,
        "executable": executable,
        "audit": audit,
        "rejected": rejected,
        "ctx": ctx,
        "regime": regime.regime.value,
        "payload": payload,
    }


def run_evening(raw: dict, *, timestamp: str) -> dict:
    scope = "post_market_evening"
    log_progress(scope, "R2 晚间复盘")
    payload = _prepare_payload(raw)
    update_concept_tracker_state(payload)
    regime = detect_regime(payload)
    today = cn_date_str()
    r2 = load_r2_config()
    research_body = ""

    try:
        from quant.factors.fund_momentum import prefetch_fund_flow

        prefetch_fund_flow(today)
    except Exception:
        pass

    sectors = build_sector_rows(
        payload,
        date_str=today,
        persist_history=True,
        allow_live_ak=True,
    )
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

    if (r2.get("research") or {}).get("enabled", True):
        try:
            research_body = build_evening_research(payload, sectors=sectors, date_str=today)
        except Exception:
            research_body = ""

    if (r2.get("research") or {}).get("prefetch_ak_on_evening", True):
        try:
            from quant.data.akshare_client import AkShareClient

            client = AkShareClient()
            names: set[tuple[str, str]] = set()
            for s in sectors:
                if getattr(s, "eligible", False):
                    sec = BOARD_INDUSTRY if s.section == BOARD_INDUSTRY else BOARD_CONCEPT
                    names.add((s.name, sec))
            for m in combat[:10]:
                for tag in m.sector_tags[:1]:
                    names.add((tag, BOARD_CONCEPT))
            client.prefetch_combat_breadth(list(names), date_str=today)
        except Exception:
            pass

    save_derived("pool_stats.json", stats)
    save_raw("post_market_evening", payload)
    log_progress_done(
        scope,
        "R2 晚间完成",
        detail=f"tracking={stats['tracking']} combat={stats['combat']}",
    )
    return {
        "stats": stats,
        "regime": regime.regime.value,
        "research": research_body,
        "payload": payload,
    }


def run_lunch(raw: dict, *, timestamp: str) -> dict:
    payload = _inject_combat(_prepare_payload(raw))
    regime = detect_regime(payload)
    save_raw("post_market_lunch", payload)
    return {"regime": regime.regime.value, "payload": payload}
