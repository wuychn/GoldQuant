"""标签生成。"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

from quant.scoring.tech_indicators import quote_change_pct
from quant.store.paths import QUANT_HOME


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _dates() -> list[str]:
    root = QUANT_HOME / "daily"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def _load_payload(path: Path) -> dict | None:
    obj = _read_json(path)
    if not isinstance(obj, dict):
        return None
    inner = obj.get("data")
    return inner if isinstance(inner, dict) else obj


def _build_sell_pnl_by_date(dates: list[str]) -> dict[str, dict[str, float]]:
    """date -> {code: sell_pnl}"""
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for d in dates:
        trades = _read_json(QUANT_HOME / "daily" / d / "trades" / "executed.json")
        if not isinstance(trades, list):
            continue
        for t in trades:
            if str(t.get("方向", "")) != "卖出":
                continue
            code = str(t.get("股票代码", "")).strip()
            if not code:
                continue
            try:
                pnl = float(t.get("已实现盈亏", 0) or 0)
            except (TypeError, ValueError):
                continue
            out[d][code] = out[d].get(code, 0.0) + pnl
    return out


def _build_fwd1_label(dates: list[str]) -> dict[tuple[str, str], float]:
    """(code, date) -> 1/0 from next trading day quote change."""
    out: dict[tuple[str, str], float] = {}
    for i, d in enumerate(dates[:-1]):
        nxt = dates[i + 1]
        raw_dir = QUANT_HOME / "daily" / nxt / "raw"
        chg_by_code: dict[str, float] = {}
        for name in ("evening.json", "pre_market.json"):
            payload = _load_payload(raw_dir / name)
            if not isinstance(payload, dict):
                continue
            for key in ("自选股", "持仓股", "同花顺人气榜"):
                for row in payload.get(key) or []:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("股票代码", "")).strip()
                    if not code or code in chg_by_code:
                        continue
                    chg = quote_change_pct(row)
                    if chg is not None:
                        chg_by_code[code] = chg
        for code, chg in chg_by_code.items():
            out[(code, d)] = 1.0 if chg > 0 else 0.0
    return out


def _label_from_pnl_window(
    code: str,
    date_str: str,
    dates: list[str],
    date_idx: dict[str, int],
    sell_pnl: dict[str, dict[str, float]],
) -> float | None:
    i = date_idx.get(date_str)
    if i is None:
        return None
    pnl = 0.0
    found = False
    for d in dates[i : i + 6]:
        p = sell_pnl.get(d, {}).get(code)
        if p is None:
            continue
        pnl += p
        found = True
    if not found:
        return None
    return 1.0 if pnl > 0 else 0.0


def attach_labels(df: pd.DataFrame) -> pd.DataFrame:
    dates = _dates()
    date_idx = {d: i for i, d in enumerate(dates)}
    sell_pnl = _build_sell_pnl_by_date(dates)
    fwd1 = _build_fwd1_label(dates)

    labels: list[float | None] = []
    for code, d in zip(df["code"].astype(str), df["date"].astype(str)):
        y = _label_from_pnl_window(code, d, dates, date_idx, sell_pnl)
        if y is None:
            y = fwd1.get((code, d))
        labels.append(y)

    out = df.copy()
    out["label"] = labels
    return out.dropna(subset=["label"])
