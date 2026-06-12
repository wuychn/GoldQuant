"""Payload 数据新鲜度与完整性校验。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from quant.scoring.tech_indicators import quote_last_price
from quant.timeutil import cn_datetime_str


@dataclass
class DataQualityReport:
    ok: bool = True
    block_intraday_buy: bool = False
    block_execute: bool = False
    issues: list[str] = field(default_factory=list)
    checked_at: str = ""
    mode: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _minute_bar_quality(stocks: list[dict], *, min_bars: int = 5) -> list[str]:
    issues: list[str] = []
    for stock in stocks:
        code = str(stock.get("股票代码", "")).strip()
        bars = stock.get("分钟行情") or []
        if not isinstance(bars, list) or len(bars) < min_bars:
            issues.append(f"{code} 分钟行情不足({len(bars) if isinstance(bars, list) else 0}<{min_bars})")
            continue
        nonzero_vol = sum(
            1
            for b in bars
            if isinstance(b, dict) and float(str(b.get("成交量") or 0).replace(",", "") or 0) > 0
        )
        if nonzero_vol < min_bars // 2:
            issues.append(f"{code} 分钟成交量异常")
    return issues


def assess_payload_quality(payload: dict, *, mode: str = "") -> DataQualityReport:
    """校验单次 API payload；盘中模式失败时阻断分时买入过滤链。"""
    report = DataQualityReport(mode=mode, checked_at=cn_datetime_str())
    issues: list[str] = []

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
            if quote_last_price(stock) is None:
                issues.append(f"{label} {code} 无有效现价")

    if mode == "during_market" and watch:
        issues.extend(_minute_bar_quality(watch))

    report.issues = issues
    critical = any(
        x in " ".join(issues)
        for x in ("缺少大盘指数", "涨跌幅缺失", "缺少赚钱效应")
    )
    stale_intraday = any("分钟" in x for x in issues)

    if critical:
        report.ok = False
        report.block_intraday_buy = True
        report.block_execute = mode == "during_market"
    elif stale_intraday:
        report.ok = False
        report.block_intraday_buy = True
    else:
        report.ok = len(issues) == 0

    return report
