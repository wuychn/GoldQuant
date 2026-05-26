"""~/.quant/state 热状态读写。

源文件（程序唯一写入点）
  optional.jsonl  自选股
  holding.jsonl   持仓
  account.json    可用/市值/总资产/当日盈亏
  stoploss.jsonl  止损冷却记录

views/*.md 由本模块在 save_optional/save_holdings/save_account 时自动生成。
API quant_endpoint 读取 optional/holding 路径为 state/ 下 JSONL。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from quant.store.paths import (
    ensure_layout,
    state_file,
    view_file,
    memory_file,
)
from quant.store.views import render_holding_md, render_optional_md

INITIAL_CAPITAL = 10000.0
STRATEGY_TAGS = frozenset({"涨停板战法", "龙回头战法", "主升浪战法", "趋势"})


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_json_atomic(path: Path, obj: Any) -> None:
    _write_text_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _parse_jsonl(text: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            rows.extend(x for x in obj if isinstance(x, dict))
        elif isinstance(obj, dict):
            rows.append(obj)
    return rows


def _normalize_rows(rows: list[dict] | None) -> list[dict]:
    out: list[dict] = []
    for item in rows or []:
        code = str(item.get("股票代码", "")).strip()
        if not code:
            continue
        row = dict(item)
        row["股票代码"] = code
        out.append(row)
    return out


def read_jsonl(path: Path) -> list[dict]:
    return _normalize_rows(_parse_jsonl(_read_text(path)))


def write_jsonl(path: Path, rows: list[dict]) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    _write_text_atomic(path, "\n".join(lines) + ("\n" if lines else ""))


def get_optional() -> list[dict]:
    ensure_layout()
    return read_jsonl(state_file("optional.jsonl"))


def get_holdings() -> list[dict]:
    ensure_layout()
    return read_jsonl(state_file("holding.jsonl"))


def save_optional(rows: list[dict], *, delta: dict | None = None) -> None:
    ensure_layout()
    write_jsonl(state_file("optional.jsonl"), rows)
    _write_text_atomic(view_file("optional.md"), render_optional_md(rows))
    if delta:
        hist = state_file("optional_history.jsonl")
        with open(hist, "a", encoding="utf-8") as f:
            f.write(json.dumps({"时间": datetime.now().isoformat(), **delta}, ensure_ascii=False) + "\n")


def save_holdings(rows: list[dict]) -> None:
    ensure_layout()
    write_jsonl(state_file("holding.jsonl"), rows)
    _write_text_atomic(view_file("holding.md"), render_holding_md(rows))


def read_stoploss() -> list[dict]:
    return read_jsonl(state_file("stoploss.jsonl"))


def append_stoploss(code: str, name: str, reason: str) -> None:
    row = {
        "股票代码": code,
        "股票名称": name,
        "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "原因": reason[:120],
    }
    with open(state_file("stoploss.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def stoploss_cooldown_codes(days: int = 3) -> set[str]:
    cutoff = datetime.now().date().toordinal() - days + 1
    codes: set[str] = set()
    for row in read_stoploss():
        code = str(row.get("股票代码", "")).strip()
        ts = str(row.get("时间", ""))[:10]
        try:
            d = date.fromisoformat(ts)
        except ValueError:
            continue
        if code and d.toordinal() >= cutoff:
            codes.add(code)
    return codes


def holding_codes_bought_today(holdings: list[dict], today: date | None = None) -> set[str]:
    today = today or datetime.now().date()
    locked: set[str] = set()
    for h in holdings:
        code = str(h.get("股票代码", "")).strip()
        ts = str(h.get("买入时间", ""))[:10]
        try:
            if code and date.fromisoformat(ts) == today:
                locked.add(code)
        except ValueError:
            continue
    return locked


def _default_account() -> dict:
    return {
        "可用资金": INITIAL_CAPITAL,
        "持仓市值": 0.0,
        "总资产": INITIAL_CAPITAL,
        "当日已实现盈亏": 0.0,
        "累计已实现盈亏": 0.0,
    }


def get_account() -> dict:
    ensure_layout()
    path = state_file("account.json")
    if not path.is_file():
        acc = _default_account()
        _write_json_atomic(path, acc)
        return acc
    try:
        data = json.loads(_read_text(path))
        return data if isinstance(data, dict) else _default_account()
    except json.JSONDecodeError:
        return _default_account()


def get_cash() -> float:
    return float(get_account().get("可用资金", INITIAL_CAPITAL))


def get_total_assets() -> float:
    return float(get_account().get("总资产", INITIAL_CAPITAL))


def compute_holdings_market_value(holdings: list[dict]) -> float:
    total = 0.0
    for h in holdings:
        qty = int(h.get("持仓股数", 0) or 0)
        if qty <= 0:
            continue
        pk = h.get("盘口") if isinstance(h.get("盘口"), dict) else {}
        price = pk.get("最新", h.get("买入价", 0))
        try:
            total += qty * float(price)
        except (TypeError, ValueError):
            continue
    return total


def merge_holdings_by_code(holdings: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for h in holdings:
        code = str(h.get("股票代码", "")).strip()
        if not code:
            continue
        if code not in merged:
            merged[code] = dict(h)
            continue
        old = merged[code]
        q1 = int(old.get("持仓股数", 0) or 0)
        q2 = int(h.get("持仓股数", 0) or 0)
        p1 = float(old.get("买入价", 0) or 0)
        p2 = float(h.get("买入价", 0) or 0)
        total_q = q1 + q2
        avg = (p1 * q1 + p2 * q2) / total_q if total_q else p2
        old["持仓股数"] = total_q
        old["买入价"] = round(avg, 4)
    return list(merged.values())


def save_account(
    *,
    cash: float,
    position_mv: float,
    daily_realized_delta: float = 0.0,
) -> dict:
    acc = get_account()
    acc["可用资金"] = round(max(0.0, cash), 4)
    acc["持仓市值"] = round(max(0.0, position_mv), 4)
    acc["总资产"] = round(acc["可用资金"] + acc["持仓市值"], 4)
    if daily_realized_delta:
        acc["当日已实现盈亏"] = round(float(acc.get("当日已实现盈亏", 0)) + daily_realized_delta, 4)
        acc["累计已实现盈亏"] = round(float(acc.get("累计已实现盈亏", 0)) + daily_realized_delta, 4)
    _write_json_atomic(state_file("account.json"), acc)
    _write_text_atomic(view_file("fund.md"), _format_num(acc["可用资金"]))
    _write_text_atomic(view_file("position_value.md"), _format_num(acc["持仓市值"]))
    return acc


def _format_num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def append_trade(date_str: str, record: dict) -> None:
    from quant.store.snapshot import daily_trades_path

    path = daily_trades_path("executed.json", date_str)
    rows: list[dict] = []
    if path.is_file():
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows = []
    if not isinstance(rows, list):
        rows = []
    rows.append(record)
    _write_json_atomic(path, rows)


def sum_today_realized_pnl(date_str: str | None = None) -> float:
    from quant.store.snapshot import daily_trades_path

    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    path = daily_trades_path("executed.json", date_str)
    if not path.is_file():
        return 0.0
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0.0
    total = 0.0
    for r in rows or []:
        try:
            total += float(r.get("已实现盈亏", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def read_news_summary() -> str:
    return _read_text(memory_file("news_summary.txt")).strip()


def write_news_summary(text: str) -> None:
    ensure_layout()
    _write_text_atomic(memory_file("news_summary.txt"), text)


def read_lessons() -> str:
    return _read_text(memory_file("lessons.md")).strip()


def append_lesson(text: str) -> None:
    ensure_layout()
    path = memory_file("lessons.md")
    old = _read_text(path).rstrip()
    block = f"\n\n## {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{text.strip()}\n"
    _write_text_atomic(path, (old + block).lstrip())
