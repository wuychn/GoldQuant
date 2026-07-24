"""合并自选股行情与持仓字段（持仓不覆盖盘口价）。"""

from __future__ import annotations

from quant.scoring.tech_indicators import quote_last_price

_HOLDING_KEYS = (
    "买入价",
    "买入时间",
    "持仓股数",
    "买入日期",
    "买入原因",
    "战法",
    "买入类型",
)


def build_stock_by_code(payload: dict) -> dict[str, dict]:
    """构建 code→行情行；自选股优先保留有效报价，持仓只叠加字段。"""
    out: dict[str, dict] = {}
    for row in payload.get("自选股") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("股票代码", "")).strip()
        if code:
            out[code] = dict(row)
    for row in payload.get("持仓股") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("股票代码", "")).strip()
        if not code:
            continue
        if code in out:
            merged = dict(out[code])
            for k in _HOLDING_KEYS:
                if row.get(k) is not None:
                    merged[k] = row[k]
            if (quote_last_price(out[code]) or 0) <= 0 and (quote_last_price(row) or 0) > 0:
                merged.update({k: v for k, v in row.items() if k not in merged or merged[k] in (None, "", 0)})
            out[code] = merged
        else:
            out[code] = dict(row)
    return out
