"""R2 状态持久化。"""

from __future__ import annotations

import json
import tempfile
import os
from pathlib import Path
from typing import Any

from quant.r2.domain.models import PoolMember, PoolStage, SectorRow
from quant.store.paths import quant_home


def r2_state_dir() -> Path:
    p = quant_home() / "state" / "r2"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_regime(data: dict) -> None:
    _write_json(r2_state_dir() / "regime.json", data)


def load_regime() -> dict:
    return _read_json(r2_state_dir() / "regime.json") or {}


def save_sector_snapshot(rows: list[SectorRow]) -> None:
    payload = [
        {
            "name": r.name,
            "section": r.section,
            "rank_gain": r.rank_gain,
            "rank_fund": r.rank_fund,
            "change_pct": r.change_pct,
            "net_flow": r.net_flow,
            "days_on_gain_board": r.days_on_gain_board,
            "lifecycle": r.lifecycle.value,
            "defensive": r.defensive,
            "eligible": r.eligible,
            "note": r.note,
        }
        for r in rows
    ]
    _write_json(r2_state_dir() / "sector_snapshot.json", payload)


def load_sector_snapshot() -> list[dict]:
    raw = _read_json(r2_state_dir() / "sector_snapshot.json")
    return raw if isinstance(raw, list) else []


def _pool_to_json(members: list[PoolMember]) -> list[dict]:
    return [
        {
            "股票代码": m.code,
            "股票名称": m.name,
            "pool_stage": m.stage.value,
            "structure_score": m.structure_score,
            "sector_tags": m.sector_tags,
            "first_seen": m.first_seen,
            "promoted_at": m.promoted_at,
            "reason": m.reason,
            "snapshot": m.snapshot,
        }
        for m in members
    ]


def _pool_from_json(raw: list) -> list[PoolMember]:
    out: list[PoolMember] = []
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("股票代码") or "").strip()
        if not code:
            continue
        stage_raw = str(row.get("pool_stage") or PoolStage.TRACKING.value)
        stage = PoolStage.COMBAT if stage_raw == PoolStage.COMBAT.value else PoolStage.TRACKING
        out.append(
            PoolMember(
                code=code,
                name=str(row.get("股票名称") or "").strip(),
                stage=stage,
                structure_score=float(row.get("structure_score") or 0),
                sector_tags=list(row.get("sector_tags") or []),
                first_seen=str(row.get("first_seen") or ""),
                promoted_at=str(row.get("promoted_at") or ""),
                reason=str(row.get("reason") or ""),
                snapshot=dict(row.get("snapshot") or {}) if isinstance(row.get("snapshot"), dict) else {},
            )
        )
    return out


def save_tracking(members: list[PoolMember]) -> None:
    _write_json(r2_state_dir() / "tracking_pool.json", _pool_to_json(members))


def save_combat(members: list[PoolMember]) -> None:
    _write_json(r2_state_dir() / "combat_pool.json", _pool_to_json(members))


def load_tracking() -> list[PoolMember]:
    return _pool_from_json(_read_json(r2_state_dir() / "tracking_pool.json") or [])


def load_combat() -> list[PoolMember]:
    return _pool_from_json(_read_json(r2_state_dir() / "combat_pool.json") or [])


def combat_as_optional_rows(members: list[PoolMember]) -> list[dict]:
    """供 enrich / 成交使用的自选股形态。"""
    return [
        {
            "股票代码": m.code,
            "股票名称": m.name,
            "pool_stage": m.stage.value,
            "structure_score": m.structure_score,
            "sector_tags": m.sector_tags,
            "加入自选原因": m.reason or "R2作战池",
        }
        for m in members
    ]


def _quote_index(payload: dict) -> dict[str, dict]:
    from quant.r2.io.payload import popularity_rows, stock_rows

    idx: dict[str, dict] = {}
    for row in stock_rows(payload, "自选股", "持仓股", "同花顺人气榜", "人气榜", "涨停池"):
        code = str(row.get("股票代码") or row.get("代码") or "").strip()
        if code:
            merged = dict(idx.get(code) or {})
            merged.update(row)
            merged.setdefault("股票代码", code)
            idx[code] = merged
    if not idx:
        for row in popularity_rows(payload):
            code = str(row.get("股票代码") or row.get("代码") or "").strip()
            if code:
                merged = dict(idx.get(code) or {})
                merged.update(row)
                merged.setdefault("股票代码", code)
                idx[code] = merged
    return idx


def _evening_payload(date_str: str) -> dict | None:
    if not date_str:
        return None
    path = quant_home() / "daily" / date_str / "raw" / "evening.json"
    if not path.is_file():
        return None
    raw = _read_json(path)
    if not isinstance(raw, dict):
        return None
    inner = raw.get("data")
    return inner if isinstance(inner, dict) else raw


def inject_combat_watchlist(
    payload: dict,
    combat: list[PoolMember] | None = None,
    *,
    date_str: str | None = None,
) -> dict:
    """将作战池注入自选股，并合并 evening 快照 + 盘中行情 enrich。"""
    members = combat if combat is not None else load_combat()
    out = dict(payload)
    quotes = _quote_index(payload)
    evening = _evening_payload(date_str or "")
    if evening:
        for code, row in _quote_index(evening).items():
            base = dict(quotes.get(code) or {})
            base.update(row)
            quotes[code] = base

    rows: list[dict] = []
    for m in members:
        base = dict(m.snapshot or quotes.get(m.code) or {})
        live = quotes.get(m.code) or {}
        if live:
            base = {**base, **live}
        base.update(
            {
                "股票代码": m.code,
                "股票名称": m.name or base.get("股票名称", ""),
                "pool_stage": m.stage.value,
                "structure_score": m.structure_score,
                "sector_tags": m.sector_tags,
                "加入自选原因": m.reason or "R2作战池",
            }
        )
        rows.append(base)
    out["自选股"] = rows
    return out


def reset_pools() -> None:
    save_tracking([])
    save_combat([])
