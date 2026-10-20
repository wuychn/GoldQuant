"""严重异常波动：只认交易所「严重」档，普通异常波动不作为进场条件。

偏离值 = 个股当日涨跌幅 − 对应指数当日涨跌幅。
严重异常波动（上涨）：
  - 连续 10 日内 N 次同向异常波动（主板/ST 4 次，双创/北交所 3 次；单次异常命中后次日重算）
  - 或 10 日累计偏离值 ≥ +100%
  - 或 30 日累计偏离值 ≥ +200%
单次异常波动：连续 3 日偏离值累计达主板 ±20% / ST ±12% / 双创·北交所 ±30%，只作上面次数的计数，本身不触发模式 A。
无涨跌幅限制日不纳入窗口。触发当日不买，调整从次日算。
"""

from __future__ import annotations

from quant.tactic.bars import Bar
from quant.tactic.limits import board_limit_pct, is_chi_next


def is_bj(code: str) -> bool:
    c = str(code or "").strip().zfill(6)
    return c.startswith(("92", "83", "87", "88", "43")) or (
        c.startswith("8") and not c.startswith("688")
    )


def board_index_code(code: str) -> str:
    c = str(code or "").strip().zfill(6)
    if c.startswith("300"):
        return "399102"
    if c.startswith("688"):
        return "000688"
    if is_bj(c):
        return "899050"
    if c.startswith(("000", "001", "002", "003")):
        return "399107"
    return "000002"


def index_code_fallbacks(code: str) -> list[str]:
    primary = board_index_code(code)
    chain = {
        "399102": ["399102", "399006", "000300"],
        "000688": ["000688", "000300"],
        "399107": ["399107", "000300"],
        "000002": ["000002", "000300"],
        "899050": ["899050", "000300"],
    }
    return list(chain.get(primary, [primary, "000300"]))


def abnormal_threshold(code: str, name: str | None = None) -> float:
    n = str(name or "")
    if any(m in n.upper() for m in ("ST", "*ST")):
        return 0.12
    if is_chi_next(code) or is_bj(code):
        return 0.30
    return 0.20


def severe_hit_count(code: str) -> int:
    return 3 if (is_chi_next(code) or is_bj(code)) else 4


def _stock_ret(b: Bar) -> float | None:
    if b.prev_close <= 0 or b.close <= 0:
        return None
    return b.close / b.prev_close - 1.0


def daily_devs(
    bars: list[Bar],
    code: str,
    *,
    name: str | None = None,
    index_ret: dict[str, float] | None = None,
) -> list[float | None]:
    lim = board_limit_pct(code, name)
    out: list[float | None] = []
    for b in bars:
        sr = _stock_ret(b)
        if sr is None:
            out.append(None)
            continue
        if abs(sr) > lim + 0.012:
            out.append(None)
            continue
        ir = 0.0
        if index_ret is not None:
            v = index_ret.get(b.date)
            if v is not None:
                ir = float(v)
        out.append(sr - ir)
    return out


def _window_sum(vals: list[float | None], end: int, n: int) -> float | None:
    if end + 1 < n:
        return None
    w = vals[end + 1 - n : end + 1]
    if any(x is None for x in w):
        return None
    return float(sum(w))  # type: ignore[arg-type]


def abnormal_hit_ends(devs: list[float | None], thr: float) -> list[int]:
    """上涨异常波动窗口结束日；命中后从次一交易日重算。"""
    hits: list[int] = []
    i = 0
    n = len(devs)
    while i + 2 < n:
        w = devs[i : i + 3]
        if any(x is None for x in w):
            i += 1
            continue
        if sum(w) >= thr - 1e-12:  # type: ignore[arg-type]
            hits.append(i + 2)
            i += 3
            continue
        i += 1
    return hits


def _severe_why(
    devs: list[float | None],
    end: int,
    hits: list[int],
    n_times: int,
) -> str:
    reasons: list[str] = []
    s10 = _window_sum(devs, end, 10)
    if s10 is not None and s10 >= 1.0 - 1e-12:
        reasons.append("10日偏离+100%")
    s30 = _window_sum(devs, end, 30)
    if s30 is not None and s30 >= 2.0 - 1e-12:
        reasons.append("30日偏离+200%")
    lo = end - 9
    if any(h == end for h in hits) and sum(1 for h in hits if lo <= h <= end) >= n_times:
        reasons.append(f"10日{n_times}次同向")
    return "、".join(reasons)


def first_yidong_index(
    bars: list[Bar],
    code: str,
    *,
    index_ret: dict[str, float] | None = None,
    name: str | None = None,
) -> int | None:
    """首次严重异常波动日。普通 3 日异常波动不返回。"""
    if len(bars) < 3:
        return None
    nm = name if name is not None else (bars[-1].name if bars else "")
    thr = abnormal_threshold(code, nm)
    n_times = severe_hit_count(code)
    devs = daily_devs(bars, code, name=nm, index_ret=index_ret)
    hits = abnormal_hit_ends(devs, thr)
    for end in range(len(devs)):
        if _severe_why(devs, end, hits, n_times):
            return end
    return None


def severe_on_last(
    bars: list[Bar],
    code: str,
    *,
    index_ret: dict[str, float] | None = None,
    name: str | None = None,
) -> tuple[bool, str]:
    if not bars:
        return False, ""
    nm = name if name is not None else bars[-1].name
    thr = abnormal_threshold(code, nm)
    devs = daily_devs(bars, code, name=nm, index_ret=index_ret)
    hits = abnormal_hit_ends(devs, thr)
    why = _severe_why(devs, len(devs) - 1, hits, severe_hit_count(code))
    return bool(why), why


def next_trigger_return(
    bars: list[Bar],
    code: str,
    *,
    index_ret_today: float = 0.0,
    name: str | None = None,
    index_ret: dict[str, float] | None = None,
) -> tuple[float | None, str]:
    """下一交易日涨幅达到多少会触发严重异常波动。

    ``bars`` 不含该日。返回 (涨幅, 原因)；涨停仍不够则 (None, "")。
    指数未知时 ``index_ret_today=0``（平盘）。
    """
    nm = name if name is not None else (bars[-1].name if bars else "")
    thr = abnormal_threshold(code, nm)
    n_times = severe_hit_count(code)
    lim = board_limit_pct(code, nm)
    devs = daily_devs(bars, code, name=nm, index_ret=index_ret)
    hits = abnormal_hit_ends(devs, thr)
    ir = float(index_ret_today or 0.0)
    cands: list[tuple[float, str]] = []
    if len(devs) >= 9 and all(x is not None for x in devs[-9:]):
        cands.append((1.0 - float(sum(devs[-9:])) + ir, "10日偏离+100%"))
    if len(devs) >= 29 and all(x is not None for x in devs[-29:]):
        cands.append((2.0 - float(sum(devs[-29:])) + ir, "30日偏离+200%"))
    today_i = len(devs)
    last_end = hits[-1] if hits else None
    reachable = last_end is None or (today_i - 2) >= last_end + 1
    if reachable and len(devs) >= 2 and devs[-1] is not None and devs[-2] is not None:
        need = thr - float(devs[-2]) - float(devs[-1])
        prior = sum(1 for h in hits if today_i - 9 <= h <= today_i - 1)
        if prior + 1 >= n_times:
            cands.append((need + ir, f"10日{n_times}次同向"))
    feasible = [(r, w) for r, w in cands if r <= lim + 1e-9]
    if not feasible:
        return None, ""
    return min(feasible, key=lambda x: x[0])


def settle_for_next_session(info: dict, *, hit_why: str, halted: bool) -> dict:
    """收盘写次日计划：触发日、停牌不删除。解除或复牌后沿用 A/B。"""
    out = dict(info)
    reasons = list(out.get("reasons") or [])
    was_unclassified = bool(out.get("fail")) or out.get("mode") not in ("A", "B")
    if halted:
        out.update(mode="observe", buy=False, fail=False, severe_state="halt")
        out["reasons"] = ["停牌，观察；复牌后重归A/B"]
        return out
    if hit_why:
        out["fail"] = False
        out["severe_state"] = "hit"
        note = f"严重异常波动（{hit_why}）已触发，次日解除后重归A/B"
        if was_unclassified:
            out["mode"] = "observe"
            out["buy"] = False
        out["reasons"] = [note] + [r for r in reasons if r != note]
        return out
    out["severe_state"] = ""
    return out


def pick_severe(rows: list[dict], code: str, *, session: str) -> dict | None:
    """从东财列表里取该股一条。``session=next`` 没有次日卡片时回退当日。"""
    c = str(code or "").strip().zfill(6)
    mine = [r for r in rows or [] if str(r.get("code") or "").zfill(6) == c]
    chosen = [r for r in mine if r.get("session") == session]
    if not chosen and session == "next":
        chosen = [r for r in mine if r.get("session") == "today"]
    if not chosen and session == "today":
        chosen = mine
    if not chosen:
        return None

    def _gap(row: dict) -> float:
        tgt = float(row.get("target_ret") or 0)
        chg = float(row.get("change_ret") or 0)
        return tgt - chg if tgt >= 0 else chg - tgt

    hits = [r for r in chosen if r.get("hit")]
    return min(hits or chosen, key=_gap)


def stamp_em_severe(info: dict, rows: list[dict], code: str, *, session: str) -> dict:
    """把东财临界涨幅写进计划。不在名单里则没有临界值。"""
    out = dict(info)
    today = pick_severe(rows, code, session="today")
    shown = pick_severe(rows, code, session=session)
    hit_row = today if today and today.get("hit") else None
    out["severe_today"] = bool(hit_row)
    out["severe_why"] = str(hit_row.get("type_name") or "") if hit_row else ""
    if shown is None:
        out["severe_next_ret"] = None
        out["severe_next_why"] = ""
        out["severe_days"] = None
        out["severe_dev"] = None
        return out
    out["severe_next_ret"] = shown.get("target_ret")
    out["severe_next_why"] = shown.get("type_name") or ""
    out["severe_days"] = shown.get("days")
    out["severe_dev"] = shown.get("deviation")
    return out


def guard_live(info: dict, *, hit_why: str, halted: bool) -> dict:
    """盘中：当日触发或停牌改为观察，不从池里删除。"""
    out = dict(info)
    if halted:
        out.update(mode="observe", buy=False, fail=False, severe_state="halt")
        out["reasons"] = ["停牌，观察；复牌后重归A/B"]
        return out
    if hit_why:
        out.update(mode="observe", buy=False, fail=False, severe_state="hit")
        out["reasons"] = [f"严重异常波动（{hit_why}）已触发，观察；解除后重归A/B"]
        return out
    out["severe_state"] = ""
    return out
