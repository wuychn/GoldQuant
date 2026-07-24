"""从 daily/raw 归档预建因子缓存（回测用，不依赖 AKShare 历史成份）。"""

from __future__ import annotations

import json
from pathlib import Path

from quant.factors.breadth_cache import prefetch_batch
from quant.factors.fund_momentum import save_fund_flow_table
from quant.io.payload import market_snapshot
from quant.scoring.theme_boards import BOARD_CONCEPT, BOARD_INDUSTRY, section_board_rows
from quant.store.paths import quant_home


def _list_dates(from_d: str | None, to_d: str | None) -> list[str]:
    root = quant_home() / "daily"
    if not root.is_dir():
        return []
    dates = sorted(p.name for p in root.iterdir() if p.is_dir())
    if from_d:
        dates = [d for d in dates if d >= from_d]
    if to_d:
        dates = [d for d in dates if d <= to_d]
    return dates


def _load_payload(path: Path) -> dict | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(obj, dict):
        inner = obj.get("data")
        return inner if isinstance(inner, dict) else obj
    return None


def _board_name(row: dict) -> str:
    return str(row.get("行业") or row.get("板块") or "").strip()


def prefetch_from_archives(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, int]:
    """遍历 evening.json，从 payload 写入 breadth / 当日资金流缓存。"""
    stats = {"dates": 0, "breadth_payload": 0, "fund_rows": 0, "sectors": 0}
    top_n = 15

    for d in _list_dates(from_date, to_date):
        evening_path = quant_home() / "daily" / d / "raw" / "evening.json"
        if not evening_path.is_file():
            continue
        payload = _load_payload(evening_path)
        if not payload:
            continue
        stats["dates"] += 1

        items: list[tuple[str, str, dict]] = []
        gain_c = section_board_rows(payload, BOARD_CONCEPT, "涨幅榜", limit=top_n)
        fund_c = section_board_rows(payload, BOARD_CONCEPT, "资金流入榜", limit=top_n)
        gain_i = section_board_rows(payload, BOARD_INDUSTRY, "涨幅榜", limit=top_n)

        names: set[str] = set()
        for row in gain_c + fund_c:
            n = _board_name(row)
            if n:
                names.add(n)
                gain_row = next((r for r in gain_c if _board_name(r) == n), row)
                items.append((n, BOARD_CONCEPT, gain_row))

        for row in gain_i:
            n = _board_name(row)
            if n and n not in names:
                items.append((n, BOARD_INDUSTRY, dict(row)))

        batch = prefetch_batch(items, date_str=d, allow_live_ak=False)
        stats["breadth_payload"] += batch.get("payload", 0) + batch.get("cached", 0)
        stats["sectors"] += len(items)

        # 归档当日板块净额 → fund flow 缓存（供动量因子读取）
        for section, gain_rows, fund_rows, stype in (
            (BOARD_CONCEPT, gain_c, fund_c, "概念资金流"),
            (BOARD_INDUSTRY, gain_i, [], "行业资金流"),
        ):
            rows_out: list[dict] = []
            seen: set[str] = set()
            for row in gain_rows + fund_rows:
                n = _board_name(row)
                if not n or n in seen:
                    continue
                seen.add(n)
                net = row.get("净额") or row.get("净流入")
                rows_out.append({"名称": n, "行业": n, "净额": net, "涨跌幅": row.get("行业-涨跌幅") or row.get("涨跌幅")})
            if rows_out:
                save_fund_flow_table(d, "今日", stype, rows_out, source="archive")
                stats["fund_rows"] += len(rows_out)

        # 衍生归档
        derived_dir = quant_home() / "daily" / d / "derived"
        derived_dir.mkdir(parents=True, exist_ok=True)
        snap = {"date": d, "regime": market_snapshot(payload), "sector_count": len(items)}
        (derived_dir / "factor_prefetch.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return stats
