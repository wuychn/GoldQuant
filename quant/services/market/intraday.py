"""盘中撮合：从 modes 抽离，session 先于 payload 构建。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from quant.push.format import ICON_ORDER, ICON_TIP, ICON_WATCH, icon_section


@dataclass
class IntradaySession:
    buy_block: str = ""
    sell_block: str = ""
    trades_executed: int = 0
    trades_rejected: dict[str, str] = field(default_factory=dict)
    ok: bool = True
    error: str = ""


def run_intraday_session(*, theta: float = 1.0) -> IntradaySession:
    """先卖后买；返回推送块与会话统计。"""
    session = IntradaySession()
    try:
        session.sell_block = _intraday_sell_block()
        buy_block, stats = _intraday_buy_block_with_stats(theta=theta)
        session.buy_block = buy_block
        session.trades_executed = stats.get("n_executed", 0)
        session.trades_rejected = stats.get("rejected") or {}
    except Exception as e:  # noqa: BLE001
        session.ok = False
        session.error = str(e)
        session.buy_block = icon_section(ICON_TIP, "盘中 job 失败", [str(e)])
    return session


def _intraday_buy_block_with_stats(*, theta: float = 1.0) -> tuple[str, dict[str, Any]]:
    from quant.data.calendar import is_trading_day
    from quant.data.fetch import fetch_spot_em
    from quant.decision.paper_execute import (
        execute_intraday_buys,
        paper_home_context,
        read_battle_pool,
    )
    from quant.factors.compose import compose_intraday_alpha
    from quant.factors.library.intraday import spot_row_from_dict
    from quant.timeutil import cn_now, intraday_minutes_since_open

    stats: dict[str, Any] = {"n_executed": 0, "rejected": {}}
    today = cn_now().date()
    if not is_trading_day(today):
        return "", stats
    today_s = today.isoformat()
    with paper_home_context():
        pool = read_battle_pool(today_s)
        if not pool:
            return "", stats
        try:
            spot = fetch_spot_em()
        except Exception as e:  # noqa: BLE001
            return icon_section(ICON_TIP, "盘中择时", [f"取价失败: {e}"]), stats
        spot_by_code = {str(r.get("code")): r for r in spot.to_dict("records")}
        rows = []
        name_map: dict[str, str] = {}
        for p in pool:
            code = str(p.get("code"))
            sd = spot_by_code.get(code)
            if not sd:
                continue
            sr = spot_row_from_dict(sd)
            if sr:
                rows.append(sr)
                name_map[code] = p.get("name") or code
        if not rows:
            return "", stats
        try:
            mins_open = intraday_minutes_since_open()
        except Exception:
            mins_open = None
        if mins_open is not None and mins_open < 10:
            return icon_section(
                ICON_TIP, "盘中择时", [f"开盘 {mins_open} 分钟，跳过（噪声主导）"]
            ), stats
        alpha_z = compose_intraday_alpha(rows)
        buys = [r for r in rows if alpha_z.get(r.code, 0.0) >= theta]
        if not buys:
            top = sorted(alpha_z.items(), key=lambda kv: -kv[1])[:3]
            top_s = ", ".join(f"{name_map.get(c, c)}:{v:.2f}" for c, v in top)
            return icon_section(
                ICON_WATCH, "盘中择时", [f"作战池{len(rows)}只 无触发(θ={theta})；最强 {top_s}"]
            ), stats
        target_weights = {str(p.get("code")): float(p.get("target_weight") or 0.0) for p in pool}
        result = execute_intraday_buys(
            buys, alpha_z, name_map=name_map, today=today_s, target_weights=target_weights
        )
        stats = {
            "n_executed": result.get("n_executed", 0),
            "rejected": result.get("rejected") or {},
        }
    lines = [f"触发{len(buys)}只 → 买入{result.get('n_executed', 0)}笔"]
    for e in result.get("executed", [])[:8]:
        lines.append(f"🛒 {e['code']} x{e['qty']} @{e['fill']} {e.get('reason', '')}")
    for c, why in (result.get("rejected") or {}).items():
        lines.append(f"拒 {c}: {why}")
    return icon_section(ICON_ORDER, "盘中择时买入", lines), stats


def _intraday_sell_block() -> str:
    from quant.data.calendar import is_trading_day
    from quant.data.fetch import fetch_spot_em
    from quant.decision.paper_execute import (
        execute_intraday_sells,
        paper_home_context,
        read_sell_watch,
    )
    from quant.timeutil import cn_now

    today = cn_now().date()
    if not is_trading_day(today):
        return ""
    today_s = today.isoformat()
    with paper_home_context():
        sells = read_sell_watch(today_s)
        if not sells:
            return ""
        try:
            spot = fetch_spot_em()
        except Exception as e:  # noqa: BLE001
            return icon_section(ICON_TIP, "盘中卖出", [f"取价失败: {e}"])
        spot_by_code = {str(r.get("code")): r for r in spot.to_dict("records")}
        result = execute_intraday_sells(sells, spot_by_code, today=today_s)
    if not result.get("n_signals"):
        return icon_section(ICON_WATCH, "盘中卖出", [f"监控{len(sells)}只 无触发"])
    lines = [f"触发{result.get('n_signals')} 卖出{result.get('n_executed')}笔"]
    for e in result.get("executed", [])[:8]:
        lines.append(f"🛒 {e['code']} x{e['qty']} @{e['fill']} {e.get('reason', '')}")
    for c, why in (result.get("rejected") or {}).items():
        lines.append(f"拒 {c}: {why}")
    return icon_section(ICON_ORDER, "盘中卖出", lines)
