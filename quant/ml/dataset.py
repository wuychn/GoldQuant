"""从 ~/.quant/daily 构建 ML 训练样本。

样本定义
--------
每条样本 = 某日某只候选股的评分特征 + 选股 label（0/1）。

特征来源
  daily/{date}/derived/scores_watchlist.json
  （晚间复盘对人气榜∪涨停池的评分明细）

选股 label（唯一口径）
  下一交易日快照涨幅 > 0 → 1，否则 0
  无次日收益则跳过（禁止用策略成交盈亏做因子标签）

附属字段
  trade_pnl_label：评分日后 6 日内卖出已实现盈亏合计是否为正（仅策略评价，不进校准）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from quant.scoring.tech_indicators import hist_close, quote_change_pct

from quant.store.paths import QUANT_HOME


@dataclass
class ScoreSample:
    """单条 ML 样本。"""

    date: str
    code: str
    name: str
    total: float                      # 当日综合评分
    dim_scores: dict[str, float]      # 各维度得分，键名与 quant.yml scoring.dimensions 一致
    label: float                      # 选股：次日涨跌方向
    forward_return_pct: float | None = None  # 次日涨幅(%)
    trade_pnl_label: float | None = None     # 策略成交结果（可选，非校准用）


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _parse_date_dir(name: str) -> datetime | None:
    try:
        return datetime.strptime(name, "%Y-%m-%d")
    except ValueError:
        return None


def _stock_return_from_payload(payload: dict, code: str) -> float | None:
    """从次日 API 快照中提取该股涨跌幅(%)。"""
    if not isinstance(payload, dict):
        return None
    for key in ("自选股", "持仓股", "同花顺人气榜"):
        for row in payload.get(key) or []:
            if str(row.get("股票代码", "")).strip() != code:
                continue
            chg = quote_change_pct(row)
            if chg is not None:
                return chg
            hist = row.get("历史行情") or []
            if isinstance(hist, list) and len(hist) >= 2:
                c0 = hist_close(hist[-2])
                c1 = hist_close(hist[-1])
                if c0 and c1 and c0 > 0:
                    return (c1 - c0) / c0 * 100
    return None


def _label_from_trades(code: str, date_str: str, dates: list[str]) -> float | None:
    """用后续卖出成交盈亏打标签（策略评价附属字段）。"""
    idx = dates.index(date_str) if date_str in dates else -1
    if idx < 0:
        return None
    window = dates[idx : idx + 6]
    pnl_sum = 0.0
    found = False
    for d in window:
        trades = _read_json(QUANT_HOME / "daily" / d / "trades" / "executed.json")
        if not isinstance(trades, list):
            continue
        for t in trades:
            if str(t.get("股票代码", "")).strip() != code:
                continue
            if str(t.get("方向", "")) != "卖出":
                continue
            try:
                pnl_sum += float(t.get("已实现盈亏", 0) or 0)
                found = True
            except (TypeError, ValueError):
                continue
    if not found:
        return None
    return 1.0 if pnl_sum > 0 else 0.0


def _next_date_payload(dates: list[str], date_str: str) -> dict | None:
    """取评分日的下一交易日任意一份 raw 快照（用于选股 label）。"""
    if date_str not in dates:
        return None
    i = dates.index(date_str)
    if i + 1 >= len(dates):
        return None
    nxt = dates[i + 1]
    for name in ("evening.json", "pre_market.json", "during_market.json", "lunch.json"):
        p = QUANT_HOME / "daily" / nxt / "raw" / name
        if not p.is_file() and name == "during_market.json":
            raw_dir = QUANT_HOME / "daily" / nxt / "raw"
            if raw_dir.is_dir():
                during = sorted(raw_dir.glob("during_*.json"))
                if during:
                    p = during[0]
        payload = _read_json(p)
        if isinstance(payload, dict):
            return payload
    return None


def load_score_samples(*, min_samples: int = 100) -> list[ScoreSample]:
    """扫描全部 daily 目录，返回可用于校准的样本列表。

    min_samples 仅用于 calibrate() 判断是否足够；此处始终返回已收集的全部样本。
    """
    daily_root = QUANT_HOME / "daily"
    if not daily_root.is_dir():
        return []

    date_dirs = sorted(
        [d.name for d in daily_root.iterdir() if d.is_dir() and _parse_date_dir(d.name)],
    )
    samples: list[ScoreSample] = []

    for date_str in date_dirs:
        scores_path = daily_root / date_str / "derived" / "scores_watchlist.json"
        rows = _read_json(scores_path)
        if not isinstance(rows, list):
            continue
        next_payload = _next_date_payload(date_dirs, date_str)
        if next_payload is None:
            continue

        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("股票代码", "")).strip()
            if not code:
                continue
            try:
                total = float(row.get("总分", 0) or 0)
            except (TypeError, ValueError):
                continue

            dim_scores: dict[str, float] = {}
            for part in row.get("分项") or []:
                if not isinstance(part, dict):
                    continue
                key = str(part.get("维度", "")).strip()
                if not key:
                    continue
                try:
                    dim_scores[key] = float(part.get("得分", 0) or 0)
                except (TypeError, ValueError):
                    continue

            fwd = _stock_return_from_payload(next_payload, code)
            if fwd is None:
                continue
            label = 1.0 if fwd > 0 else 0.0
            trade_pnl_label = _label_from_trades(code, date_str, date_dirs)

            samples.append(
                ScoreSample(
                    date=date_str,
                    code=code,
                    name=str(row.get("股票名称", "")).strip(),
                    total=total,
                    dim_scores=dim_scores,
                    label=label,
                    forward_return_pct=fwd,
                    trade_pnl_label=trade_pnl_label,
                )
            )

    return samples
