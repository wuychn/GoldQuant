"""Payload 数据新鲜度与完整性校验。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from quant.scoring.tech_indicators import quote_last_price
from quant.strategy.intraday import session_minute_bars
from quant.timeutil import cn_datetime_str


@dataclass
class DataQualityReport:
    ok: bool = True
    block_intraday_buy: bool = False
    block_execute: bool = False
    skip_intraday_buy_codes: list[str] = field(default_factory=list)
    stock_issues: dict[str, list[str]] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    checked_at: str = ""
    mode: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def skip_intraday_buy_codes(payload: dict) -> set[str]:
    """盘中分时买入需跳过的股票（仅影响该代码，不连坐全池）。"""
    dq = payload.get("_data_quality") or {}
    return {str(c).strip() for c in (dq.get("skip_intraday_buy_codes") or []) if str(c).strip()}


def _minute_bar_issues(stock: dict, *, min_bars: int = 5) -> list[str]:
    code = str(stock.get("股票代码", "")).strip()
    bars = session_minute_bars(stock)
    issues: list[str] = []
    if len(bars) < min_bars:
        issues.append(f"{code} 连续竞价分钟不足({len(bars)}<{min_bars})")
        return issues
    nonzero_vol = sum(1 for b in bars if b.get("vol", 0) > 0)
    if nonzero_vol < min_bars // 2:
        issues.append(f"{code} 连续竞价分钟成交量异常")
    return issues


def assess_payload_quality(payload: dict, *, mode: str = "") -> DataQualityReport:
    """校验单次 API payload。

    - 宏观字段缺失：仍阻断全池盘中买入/成交（非单票连坐）。
    - 单票分钟 K / 现价异常：仅写入 ``skip_intraday_buy_codes``，不影响其它标的。
    """
    report = DataQualityReport(mode=mode, checked_at=cn_datetime_str())
    issues: list[str] = []
    skip_codes: list[str] = []
    stock_issues: dict[str, list[str]] = {}

    indices = payload.get("大盘指数") or []
    if not indices:
        issues.append("缺少大盘指数")
    else:
        has_chg = any(
            isinstance(row, dict) and row.get("涨跌幅") is not None for row in indices
        )
        if not has_chg:
            issues.append("大盘指数涨跌幅缺失")

    profit = payload.get("赚钱效应") or {}
    if mode in ("during_market", "pre_market", "post_market_lunch", "post_market_evening"):
        if not isinstance(profit, dict) or not profit:
            issues.append("缺少赚钱效应")

    watch = payload.get("自选股") or []
    holdings = payload.get("持仓股") or []
    for label, rows in (("自选股", watch), ("持仓股", holdings)):
        for stock in rows:
            if not isinstance(stock, dict):
                continue
            code = str(stock.get("股票代码", "")).strip()
            if not code:
                continue
            per_stock: list[str] = []
            if quote_last_price(stock) is None:
                per_stock.append(f"{label} {code} 无有效现价")
            if mode == "during_market" and label == "自选股":
                per_stock.extend(_minute_bar_issues(stock))
            if per_stock:
                stock_issues[code] = stock_issues.get(code, []) + per_stock
                if label == "自选股" and code not in skip_codes:
                    skip_codes.append(code)

    report.issues = issues
    report.stock_issues = stock_issues
    report.skip_intraday_buy_codes = skip_codes

    critical = any(
        x in " ".join(issues)
        for x in ("缺少大盘指数", "涨跌幅缺失", "缺少赚钱效应")
    )
    if critical:
        report.ok = False
        report.block_intraday_buy = True
        report.block_execute = mode == "during_market"
    else:
        report.ok = len(issues) == 0 and not stock_issues

    return report
