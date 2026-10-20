"""模式B：5板及以上自然断板后的横盘。"""

from __future__ import annotations

from quant.tactic.bars import Bar, a_kill, consecutive_close_limit_down, ma5, natural_break_index
from quant.tactic.limits import chop_cap


def _launched(bars: list[Bar], brk: int, chop: list[Bar], vol_pre5: list[float]) -> bool:
    """放量阳线（量>断板前5日均量1.5倍）或收盘从下方站上MA5。"""
    mean_pre = (sum(vol_pre5) / len(vol_pre5)) if vol_pre5 else 0.0
    prev_below = False
    for j, b in enumerate(chop):
        prefix = bars[: brk + 1 + j + 1]
        m = ma5(prefix)
        below = m is not None and b.close < m
        reclaim = prev_below and m is not None and b.close > m
        yang = b.close > b.open
        heavy = mean_pre > 0 and b.volume > 1.5 * mean_pre
        if (yang and heavy) or reclaim:
            return True
        prev_below = below
    return False


def evaluate_mode_b(bars: list[Bar], code: str, *, break_i: int | None = None) -> dict:
    brk = break_i if break_i is not None else natural_break_index(bars, code)
    if brk is None:
        return {"ok": False, "buy": False, "fail": True, "reasons": ["无自然断板"]}
    floor = bars[brk].low
    chop = bars[brk + 1 :]
    pre = bars[:brk]
    if not pre:
        return {"ok": False, "buy": False, "fail": True, "reasons": ["断板前无K线"]}

    fail: list[str] = []
    notes: list[str] = []
    if consecutive_close_limit_down(bars[brk:], code):
        fail.append("连续2日收盘跌停")
    if a_kill(bars[brk:]):
        fail.append("A杀创新低")
    last = bars[-1]
    if last.close < floor:
        fail.append("收盘跌破断板日最低价")

    peak = max(b.close for b in pre)
    trough = min((b.close for b in chop), default=last.close)
    dd = (peak - trough) / peak if peak > 0 else 0.0
    cap = chop_cap(code)
    if dd > cap:
        fail.append(f"横盘回撤{dd:.1%}>上限{cap:.0%}")

    vol_ok = True
    vol_chop = [b.volume for b in chop if b.volume > 0]
    vol_pre5 = [b.volume for b in pre[-5:] if b.volume > 0]
    if chop and vol_chop and vol_pre5:
        if sum(vol_chop) / len(vol_chop) >= 0.70 * (sum(vol_pre5) / len(vol_pre5)):
            vol_ok = False
            notes.append("横盘未缩量到70%")
        mean_c = sum(vol_chop) / len(vol_chop)
        for b in chop:
            if b.volume > 1.5 * mean_c and b.close < b.open:
                fail.append(f"{b.date}放量阴跌")
                break

    days = len(chop)
    if days < 3:
        notes.append(f"断板后仅{days}日，继续观察")
    if days > 10 and not _launched(bars, brk, chop, vol_pre5):
        fail.append("断板超10日仍未启动")

    if fail:
        return {"ok": False, "buy": False, "fail": True, "reasons": fail}

    buy = days >= 3 and last.close >= floor and vol_ok
    if buy:
        notes.append("模式B全满足，尾盘低吸≤20%")
    elif not notes:
        notes.append("模式B观察中")
    return {
        "ok": True,
        "buy": buy,
        "fail": False,
        "reasons": notes,
        "floor": floor,
        "break_date": bars[brk].date,
    }
