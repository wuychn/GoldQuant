"""特征工程。"""

from __future__ import annotations

from typing import Any

from quant.io.payload import market_snapshot, popularity_rows, stock_rows
from quant.sector.engine import build_sector_rows as sector_rows_from_payload
from quant.signals.structure import structure_score
from quant.scoring.tech_indicators import quote_change_pct, quote_last_price
from quant.strategy.main_wave import main_wave_phase
from quant.config import load_gates_config


def build_market_rows(date: str, payload: dict, *, source: str) -> list[dict]:
    ms = market_snapshot(payload)
    return [
        {
            "date": date,
            "source": source,
            "regime_label": ms.get("regime_label"),
            "zt_count": ms.get("zt_count"),
            "up_count": ms.get("up_count"),
            "down_count": ms.get("down_count"),
            "index_chg": ms.get("index_chg"),
            "zt_height": ms.get("zt_height"),
        }
    ]


def build_sector_feature_rows(date: str, payload: dict, *, source: str) -> list[dict]:
    rows = sector_rows_from_payload(payload, date_str=date, use_ak_breadth=False)
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "date": date,
                "source": source,
                "sector": r.name,
                "section": r.section,
                "rank_gain": r.rank_gain,
                "rank_fund": r.rank_fund,
                "change_pct": r.change_pct,
                "net_flow": r.net_flow,
                "lifecycle": r.lifecycle.value,
                "defensive": int(r.defensive),
                "eligible": int(r.eligible),
                "factor_score": r.factor_score,
                "rs_vs_index": r.rs_vs_index,
                "fund_score": r.fund_score,
                "breadth_score": r.breadth_score,
                "crowd_penalty": r.crowd_penalty,
            }
        )
    return out


def build_stock_rows(date: str, payload: dict, *, source: str) -> list[dict]:
    rows = stock_rows(payload, "自选股", "持仓股", "同花顺人气榜", "人气榜")
    if not rows:
        rows = popularity_rows(payload)
    cfg = load_gates_config().get("main_wave") or {}
    out: list[dict] = []
    for row in rows[:200]:
        code = str(row.get("股票代码") or "").strip()
        if not code:
            continue
        ok, phase, _ = main_wave_phase(row, cfg)
        out.append(
            {
                "date": date,
                "source": source,
                "code": code,
                "name": str(row.get("股票名称") or ""),
                "structure_score": structure_score(row),
                "in_main_wave": int(ok),
                "phase": phase or "",
                "day_chg": quote_change_pct(row),
                "last_price": quote_last_price(row),
            }
        )
    return out


def stock_feature_vector(stock: dict, payload: dict, *, regime: str = "") -> dict[str, float]:
    """运行时单行特征。"""
    cfg = load_gates_config().get("main_wave") or {}
    ok, phase, _ = main_wave_phase(stock, cfg)
    ms = market_snapshot(payload)
    feats = {
        "structure_score": structure_score(stock),
        "in_main_wave": float(ok),
        "day_chg": float(quote_change_pct(stock) or 0),
        "zt_count": float(ms.get("zt_count") or 0),
        "index_chg": float(ms.get("index_chg") or 0),
        "regime_strong": 1.0 if regime == "强势" else 0.0,
        "regime_weak": 1.0 if regime == "弱势" else 0.0,
        "phase_accel": 1.0 if phase == "加速" else 0.0,
        "phase_pullback": 1.0 if phase == "回调" else 0.0,
    }
    rank = stock.get("人气排名") or stock.get("排名")
    try:
        feats["pop_rank"] = float(rank)
    except (TypeError, ValueError):
        feats["pop_rank"] = 99.0
    return feats
