"""纸交易 / 回测 parity（code+action+kind 字段级）。"""

from __future__ import annotations

import json
from typing import Any

from quant.store.paths import QUANT_HOME


def _signal_key(row: dict) -> str:
    code = str(row.get("code") or row.get("股票代码") or "").strip()
    action = str(row.get("action") or row.get("方向") or "").strip()
    kind = str(
        row.get("signal_kind")
        or row.get("sell_type")
        or row.get("买入类型")
        or row.get("卖出类型")
        or ""
    ).strip()
    return f"{code}|{action}|{kind}"


def _code_only(row: dict) -> str:
    return str(row.get("code") or row.get("股票代码") or "").strip()


def load_daily_signals(date_str: str) -> dict[str, Any]:
    path = QUANT_HOME / "daily" / date_str / "derived" / "signals.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def compare_signals_vs_backtest(
    date_str: str,
    backtest_executable: list[dict],
) -> dict[str, Any]:
    """对比实盘落盘 signals 与回测 executable（字段级 + 代码级）。"""
    live = load_daily_signals(date_str)
    live_exec = live.get("executable") or []
    live_keys = {_signal_key(s) for s in live_exec if isinstance(s, dict)}
    bt_keys = {_signal_key(s) for s in backtest_executable if isinstance(s, dict)}
    live_keys = {k for k in live_keys if not k.startswith("|")}
    bt_keys = {k for k in bt_keys if not k.startswith("|")}

    live_codes = {_code_only(s) for s in live_exec if isinstance(s, dict)}
    bt_codes = {_code_only(s) for s in backtest_executable if isinstance(s, dict)}
    live_codes.discard("")
    bt_codes.discard("")

    key_match = len(live_keys & bt_keys)
    key_total = max(len(live_keys | bt_keys), 1)
    code_match = len(live_codes & bt_codes)
    code_total = max(len(live_codes | bt_codes), 1)

    return {
        "date": date_str,
        "parity_ratio": round(key_match / key_total, 4),
        "code_parity_ratio": round(code_match / code_total, 4),
        "only_live": sorted(live_keys - bt_keys),
        "only_backtest": sorted(bt_keys - live_keys),
        "only_live_codes": sorted(live_codes - bt_codes),
        "only_backtest_codes": sorted(bt_codes - live_codes),
        "live_count": len(live_keys),
        "backtest_count": len(bt_keys),
        "field_level": True,
    }


def run_parity_check(
    date_str: str,
    *,
    min_parity_ratio: float = 0.8,
) -> dict[str, Any]:
    """无回测 executable 时，与当日 trades 做字段级 parity。"""
    live = load_daily_signals(date_str)
    live_exec = live.get("executable") or live.get("confirmed") or []
    trades_path = QUANT_HOME / "daily" / date_str / "trades" / "executed.json"
    bt_exec: list[dict] = []
    if trades_path.is_file():
        try:
            rows = json.loads(trades_path.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                bt_exec = [
                    {
                        "code": str(t.get("股票代码", "")),
                        "action": t.get("方向"),
                        "signal_kind": t.get("信号类型") or t.get("卖出类型") or t.get("买入类型") or "",
                    }
                    for t in rows
                    if isinstance(t, dict)
                ]
        except (json.JSONDecodeError, OSError):
            pass
    result = compare_signals_vs_backtest(date_str, bt_exec)
    result["passed"] = float(result["parity_ratio"]) >= min_parity_ratio
    result["min_parity_ratio"] = min_parity_ratio
    if not live_exec and not bt_exec:
        result["passed"] = True
        result["note"] = "无信号也无成交，视为空仓一致"
    return result


def scan_parity(
    *,
    recent_days: int = 5,
    min_parity_ratio: float = 0.8,
) -> dict[str, Any]:
    """扫描最近 N 个有 daily 目录的交易日。"""
    root = QUANT_HOME / "daily"
    if not root.is_dir():
        return {"days": [], "failed": [], "reason": "无 daily 目录"}
    dates = sorted(
        [p.name for p in root.iterdir() if p.is_dir() and len(p.name) == 10],
        reverse=True,
    )[:recent_days]
    days = [run_parity_check(d, min_parity_ratio=min_parity_ratio) for d in sorted(dates)]
    failed = [d for d in days if not d.get("passed")]
    return {
        "days": days,
        "failed": failed,
        "passed": len(failed) == 0,
        "n": len(days),
    }
