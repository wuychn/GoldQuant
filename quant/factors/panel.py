"""因子面板构建与持有期收益。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quant.factors.base import FactorRow
from quant.factors.neutralize import neutralize_panel_rows
from quant.factors.raw import FACTOR_SPECS, compute_raw_factors, factor_names
from quant.factors.universe import log_mcap, stock_industry
from quant.pool.builder import build_candidates
from quant.scoring.tech_indicators import hist_close, hist_closes, quote_change_pct
from quant.store.paths import QUANT_HOME


@dataclass
class FactorPanel:
    rows: list[FactorRow] = field(default_factory=list)

    @property
    def dates(self) -> list[str]:
        return sorted({r.date for r in self.rows})

    def by_date(self, date: str) -> list[FactorRow]:
        return [r for r in self.rows if r.date == date]


def build_factor_panel(
    stocks: list[dict],
    *,
    date: str,
    payload: dict | None = None,
    neutralize: bool = True,
    min_names: int = 5,
) -> FactorPanel:
    """从当日股票列表构建截面面板。"""
    rows: list[FactorRow] = []
    for stock in stocks:
        code = str(stock.get("股票代码") or stock.get("代码") or "").strip()
        if not code:
            continue
        raw = compute_raw_factors(stock)
        if not raw:
            continue
        rows.append(
            FactorRow(
                date=date,
                code=code,
                name=str(stock.get("股票名称") or stock.get("名称") or "").strip(),
                industry=stock_industry(stock, payload),
                log_mcap=log_mcap(stock),
                raw=raw,
            )
        )
    if neutralize and rows:
        neutralize_panel_rows(rows, factor_names=factor_names(), min_names=min_names)
    return FactorPanel(rows=rows)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _evening_payload(date_str: str) -> dict | None:
    raw = QUANT_HOME / "daily" / date_str / "raw"
    for name in ("evening.json", "post_market_evening.json"):
        obj = _read_json(raw / name)
        if isinstance(obj, dict):
            data = obj.get("data")
            return data if isinstance(data, dict) else obj
    return None


def _any_payload(date_str: str) -> dict | None:
    p = _evening_payload(date_str)
    if p:
        return p
    raw = QUANT_HOME / "daily" / date_str / "raw"
    if not raw.is_dir():
        return None
    for name in ("pre_market.json", "lunch.json"):
        obj = _read_json(raw / name)
        if isinstance(obj, dict):
            data = obj.get("data")
            return data if isinstance(data, dict) else obj
    during = sorted(raw.glob("during*.json"))
    if during:
        obj = _read_json(during[0])
        if isinstance(obj, dict):
            data = obj.get("data")
            return data if isinstance(data, dict) else obj
    return None


def _find_stock(payload: dict, code: str) -> dict | None:
    code = code.strip()
    for key in ("自选股", "持仓股", "同花顺人气榜", "候选股"):
        for row in payload.get(key) or []:
            if not isinstance(row, dict):
                continue
            c = str(row.get("股票代码") or row.get("代码") or "").strip()
            if c == code:
                return row
    # 候选合并源
    for row in build_candidates(payload):
        if str(row.get("股票代码", "")).strip() == code:
            return row
    return None


def forward_return_from_hist(stock: dict, *, horizon: int = 5) -> float | None:
    """若历史行情含未来 horizon 根 K（研究用合成数据），直接算持有期收益。

    实盘落盘 hist 通常只到当日，返回 None，改走跨日 payload。
    """
    closes = hist_closes(stock.get("历史行情") or [])
    # 无法从单日 hist 得到未来；保留接口给单测注入「未来已写入」的假数据
    if len(closes) < 2 + horizon:
        return None
    # 约定：closes[-1] 为当日；若提供方把未来也写进 hist，则 closes 末尾含未来
    # 这里不猜测，返回 None，由跨日逻辑负责
    return None


def forward_return_across_days(
    dates: list[str],
    date_str: str,
    code: str,
    *,
    horizon: int = 5,
) -> float | None:
    """T 日 → T+horizon 日累计涨幅(%)；用后续日快照涨跌累加或收盘推算。"""
    if date_str not in dates:
        return None
    i = dates.index(date_str)
    if i + horizon >= len(dates):
        return None

    # 优先：T+1..T+h 每日涨跌幅累加
    cum = 0.0
    got = 0
    for j in range(1, horizon + 1):
        payload = _any_payload(dates[i + j])
        if not payload:
            continue
        row = _find_stock(payload, code)
        if not row:
            continue
        chg = quote_change_pct(row)
        if chg is None:
            hist = row.get("历史行情") or []
            if isinstance(hist, list) and len(hist) >= 2:
                c0 = hist_close(hist[-2])
                c1 = hist_close(hist[-1])
                if c0 and c1 and c0 > 0:
                    chg = (c1 - c0) / c0 * 100
        if chg is None:
            continue
        cum += float(chg)
        got += 1
    if got >= max(1, horizon // 2):
        return cum

    # 回退：T 日与 T+h 日收盘比价
    p0 = _any_payload(date_str)
    p1 = _any_payload(dates[i + horizon])
    if not p0 or not p1:
        return None
    s0 = _find_stock(p0, code)
    s1 = _find_stock(p1, code)
    if not s0 or not s1:
        return None
    c0_list = hist_closes(s0.get("历史行情") or [])
    c1_list = hist_closes(s1.get("历史行情") or [])
    if not c0_list or not c1_list or c0_list[-1] <= 0:
        return None
    return (c1_list[-1] / c0_list[-1] - 1.0) * 100


def load_factor_panels_from_daily(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    horizon: int = 5,
    neutralize: bool = True,
    min_names: int = 5,
) -> FactorPanel:
    """扫描 ~/.quant/daily 晚间候选，构建多日因子面板并挂持有期收益。"""
    root = QUANT_HOME / "daily"
    if not root.is_dir():
        return FactorPanel()
    dates = sorted(p.name for p in root.iterdir() if p.is_dir() and len(p.name) == 10)
    if from_date:
        dates = [d for d in dates if d >= from_date]
    if to_date:
        dates = [d for d in dates if d <= to_date]

    all_rows: list[FactorRow] = []
    for d in dates:
        payload = _evening_payload(d) or _any_payload(d)
        if not payload:
            continue
        stocks = build_candidates(payload)
        if len(stocks) < min_names:
            # 候选太少时用自选股兜底
            extra = [r for r in (payload.get("自选股") or []) if isinstance(r, dict)]
            by = {str(s.get("股票代码", "")).strip(): s for s in stocks}
            for r in extra:
                c = str(r.get("股票代码", "")).strip()
                if c and c not in by:
                    by[c] = r
            stocks = list(by.values())
        panel = build_factor_panel(
            stocks,
            date=d,
            payload=payload,
            neutralize=False,
            min_names=min_names,
        )
        for row in panel.rows:
            row.forward_return_pct = forward_return_across_days(
                dates, d, row.code, horizon=horizon
            )
            all_rows.append(row)

    if neutralize and all_rows:
        neutralize_panel_rows(all_rows, factor_names=factor_names(), min_names=min_names)
    return FactorPanel(rows=all_rows)


# 避免未使用告警
_ = FACTOR_SPECS
