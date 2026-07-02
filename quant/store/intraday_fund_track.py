"""盘中个股主力净额快照：跨调度轮次判断「流出收敛 / 改善」。

口径与资金流维度一致：大单流入 − 大单流出（万元）。
"""

from __future__ import annotations

import json
from typing import Any

from quant.market.fund_flow import intraday_main_net_wan
from quant.store.paths import state_file
from quant.timeutil import cn_date_str, cn_now

_FILE = "intraday_fund_snapshots.json"


def _load() -> dict[str, Any]:
    path = state_file(_FILE)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(data: dict[str, Any]) -> None:
    path = state_file(_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_net_flow(code: str, net_wan: float | None) -> None:
    """追加当日净额快照（万元）；同值连续重复不写入。"""
    c = str(code).strip()
    if not c or net_wan is None:
        return
    today = cn_date_str()
    data = _load()
    day = data.setdefault(today, {})
    hist = day.setdefault(c, [])
    if hist and abs(float(hist[-1]) - float(net_wan)) < 1e-6:
        return
    hist.append(float(net_wan))
    # 保留最近 24 次调度快照
    day[c] = hist[-24:]
    # 清理旧日期
    for k in list(data.keys()):
        if k != today:
            del data[k]
    _save(data)


def record_watchlist_fund_snapshots(stocks: list[dict]) -> None:
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        code = str(stock.get("股票代码", "")).strip()
        if not code:
            continue
        record_net_flow(code, intraday_main_net_wan(stock))


def net_flow_improving(
    code: str,
    *,
    min_delta_wan: float = 50.0,
    current_net: float | None = None,
) -> bool:
    """净额可为负，但较上一轮快照明显改善（或当前转正）。"""
    c = str(code).strip()
    if not c:
        return False
    today = cn_date_str()
    hist = (_load().get(today) or {}).get(c) or []
    if current_net is not None and hist and abs(float(hist[-1]) - float(current_net)) > 1e-6:
        hist = list(hist) + [float(current_net)]
    if len(hist) >= 2:
        return float(hist[-1]) >= float(hist[-2]) + min_delta_wan
    if len(hist) == 1:
        return float(hist[0]) > 0
    if current_net is not None:
        return float(current_net) > 0
    return False
