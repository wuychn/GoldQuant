"""模式A：严重异动后的调整。"""

from __future__ import annotations

from quant.tactic.bars import (
    Bar,
    a_kill,
    consecutive_close_limit_down,
    ma20,
    rebound_higher_high,
)
from quant.tactic.limits import is_stop_k, pullback_cap
from quant.tactic.yidong import first_yidong_index


def evaluate_mode_a(
    bars: list[Bar],
    code: str,
    *,
    yidong_i: int | None = None,
    index_ret: dict[str, float] | None = None,
) -> dict:
    """返回 {ok, buy, reasons, fail}。缺硬条件（前七条）→ fail 删。"""
    yi = yidong_i if yidong_i is not None else first_yidong_index(
        bars, code, index_ret=index_ret
    )
    if yi is None or yi + 1 >= len(bars):
        return {"ok": False, "buy": False, "fail": True, "reasons": ["无异动后调整期"]}
    adj = bars[yi + 1 :]
    pre = bars[: yi + 1]
    if not adj or not pre:
        return {"ok": False, "buy": False, "fail": True, "reasons": ["调整期为空"]}

    hard_fail: list[str] = []
    notes: list[str] = []

    if consecutive_close_limit_down(adj, code):
        hard_fail.append("连续2日收盘跌停")
    if a_kill(adj):
        hard_fail.append("A杀创新低")

    peak = max(b.close for b in pre)
    trough = min(b.close for b in adj)
    dd = (peak - trough) / peak if peak > 0 else 1.0
    cap = pullback_cap(code)
    if dd > cap:
        hard_fail.append(f"调整{dd:.1%}>上限{cap:.0%}")

    m20 = ma20(bars)
    last = bars[-1]
    if m20 is None:
        hard_fail.append("不满20日无法判均线")
    elif last.close < m20:
        notes.append("收盘跌破20日线，降级观察")

    vol_adj = [b.volume for b in adj if b.volume > 0]
    vol_pre5 = [b.volume for b in pre[-5:] if b.volume > 0]
    if vol_adj and vol_pre5:
        if sum(vol_adj) / len(vol_adj) >= 0.80 * (sum(vol_pre5) / len(vol_pre5)):
            hard_fail.append("调整期未缩量到80%")
        mean_adj = sum(vol_adj) / len(vol_adj)
        for i, b in enumerate(adj):
            stop_today = is_stop_k(b.open, b.high, b.low, b.close, b.prev_close)
            if stop_today:
                continue
            if b.volume > 1.5 * mean_adj and b.close < b.open:
                hard_fail.append(f"{b.date}放量阴跌")
                break
    else:
        hard_fail.append("量能不足")

    stop_idx = [
        i
        for i, b in enumerate(adj)
        if b.close <= trough * 1.03
        and is_stop_k(b.open, b.high, b.low, b.close, b.prev_close)
    ]
    two_stop = False
    for a in range(len(stop_idx)):
        for b in range(a + 1, len(stop_idx)):
            if stop_idx[b] - stop_idx[a] >= 2:
                two_stop = True
                break
        if two_stop:
            break
    if not two_stop:
        hard_fail.append("低位止跌K不足2根")

    higher_low = False
    if len(adj) >= 6:
        prev_lo = min(b.low for b in adj[-6:-3])
        rec_lo = min(b.low for b in adj[-3:])
        higher_low = rec_lo >= prev_lo
    if len(adj) > 15 and not higher_low:
        return {"ok": False, "buy": False, "fail": True, "reasons": ["调整超15日仍未低点抬高"]}

    if hard_fail:
        return {"ok": False, "buy": False, "fail": True, "reasons": hard_fail}

    if m20 is not None and last.close < m20:
        return {"ok": True, "buy": False, "fail": False, "reasons": notes}

    hh = rebound_higher_high(adj)
    if hh is True:
        notes.append("反弹高点抬高")

    if not higher_low:
        notes.append("低点尚未抬高")
        return {"ok": True, "buy": False, "fail": False, "reasons": notes}

    notes.append("模式A全满足，尾盘可买≤20%")
    return {"ok": True, "buy": True, "fail": False, "reasons": notes, "peak": peak, "trough": trough}
