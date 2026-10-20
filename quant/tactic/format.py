"""飞书复盘 / 盘中正文。"""

from __future__ import annotations

from quant.tactic.emotion import Emotion


def _dot(tag: str) -> str:
    return {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(tag, "⚪")


def _mode_cn(mode: object) -> str:
    return {"A": "异动调整", "B": "断板横盘"}.get(str(mode or ""), "只观察")


def _emotion_head(e: Emotion, *, review: bool) -> str:
    if e.new_entry == "block":
        text = "红灯 · 停开新仓 · 只卖不买"
        icon = "🔴"
    elif e.new_entry == "plan_only":
        text = "黄灯 · 只做下列计划 · 不临机加票" if review else "黄灯 · 只做计划内"
        icon = "🟡"
    else:
        text = "绿灯 · 可开新仓"
        icon = "🟢"
    return f"{icon} {text}"


def _emotion_bits(e: Emotion) -> str:
    zb = "—" if e.zb_rate is None else f"{e.zb_rate:.0%}"
    promo = "—" if e.promo_2to3 is None else f"{e.promo_2to3:.0%}"
    prem = "—" if e.y_zt_prem is None else f"{e.y_zt_prem:+.1f}%"
    bits = [
        f"跌停 {e.dt_n} {_dot(e.lights.get('dt', ''))}",
        f"炸板 {zb} {_dot(e.lights.get('zb', ''))}",
        f"2进3 {promo} {_dot(e.lights.get('promo', ''))}",
        f"溢价 {prem} {_dot(e.lights.get('prem', ''))}",
        f"高位跌停 {e.high_dt_n} {_dot(e.lights.get('high_dt', ''))}",
    ]
    return " · ".join(bits)


def _mark(reasons: list, *, buy: bool = False) -> str:
    text = " ".join(str(r) for r in reasons)
    if buy:
        return "▶"
    if "涨停" in text or "连板" in text:
        return "▲"
    if "规避" in text or "严重" in text or "触发" in text:
        return "⚠"
    if "停牌" in text:
        return "⏸"
    return "●"


def severe_lines(rows: list[dict], *, horizon: str) -> list[str]:
    """horizon：次日（复盘）或今日（盘中）。"""
    lines: list[str] = []
    for w in rows:
        label = f"{w.get('name', '')} {w.get('code', '')}".strip()
        bits: list[str] = []
        if w.get("severe_state") == "halt":
            bits.append("停牌，观察；复牌后重归A/B")
        if horizon == "次日" and w.get("severe_today"):
            bits.append(f"今日已触发{w.get('severe_why') or ''}，当日不买；次日解除后重归A/B")
        ret = w.get("severe_next_ret")
        why = w.get("severe_next_why") or "严重异常波动"
        if ret is None:
            if bits:
                lines.append(f"⚠ {label}\n  " + "；".join(bits))
            continue
        pct = f"{float(ret):+.2%}"
        dev = w.get("severe_dev")
        days = w.get("severe_days")
        extra = ""
        if days and dev is not None:
            extra = f"（当前{days}日偏离{float(dev):+.2%}）"
        verb = "小于" if float(ret) < 0 else "达到"
        bits.append(f"{horizon}收盘涨幅{verb}{pct}触发{why}{extra}")
        if horizon == "今日" and w.get("severe_state") == "hit":
            bits.append("已触发，转观察")
        if bits:
            lines.append(f"⚠ {label}\n  " + "\n  ".join(bits))
    return lines


def _sell_text(mode: object) -> str:
    if str(mode) == "B":
        return "约+10%或冲高回落；次日冲高即卖，不赌连板；跌停走，走不掉次日竞价走"
    return "约+10%或冲高回落；跌停走，走不掉次日竞价走"


def _section(title: str, lines: list[str], *, same_line: bool = False) -> str:
    clean = [ln for ln in lines if ln and str(ln).strip()]
    if not clean:
        return ""
    if same_line:
        rest = "\n".join(clean[1:])
        return f"【{title}】{clean[0]}" + (f"\n{rest}" if rest else "")
    return f"【{title}】\n" + "\n".join(clean)


def close_body(
    *,
    as_of: str,
    e: Emotion,
    watch: list[dict],
    plan: list[dict],
    positions: list[dict],
    plan_for: str = "",
) -> str:
    nxt = (plan_for or as_of)[5:10]
    buys = [p for p in plan if p.get("buy")]
    if e.new_entry == "block" or not buys:
        op_head = f"{nxt} 不买"
    else:
        w = int(round(float(buys[0].get("weight") or 0) * 100)) or 20
        op_head = f"{nxt} 尾盘 · 可买 {len(buys)} 只 · 单票 {w}%"
    op_lines = [op_head]
    for i, p in enumerate(plan, 1):
        mark = _mark(p.get("reasons") or [], buy=bool(p.get("buy")))
        buy = "买" if p.get("buy") else "不买"
        op_lines.append(
            f"{i}. {buy}  {mark} {p.get('name', '')} {p.get('code', '')}  {_mode_cn(p.get('mode'))}  {p.get('boards', '')}板".rstrip()
        )
        if p.get("buy"):
            op_lines.append("   · 14:30 后尾盘，涨停不追")
            op_lines.append(f"   · 卖：{_sell_text(p.get('mode'))}")
        elif p.get("reasons"):
            op_lines.append("   · " + "；".join(str(x) for x in (p.get("reasons") or [])[:2]))
    plan_codes = {str(p.get("code") or "") for p in plan}
    obs = []
    for w in watch:
        if str(w.get("code") or "") in plan_codes:
            continue
        mark = _mark(w.get("reasons") or [])
        why = "；".join(str(x) for x in (w.get("reasons") or [])[:2])
        obs.append(
            f"{mark} {w.get('name', '')} {w.get('code', '')}  {w.get('boards', '')}板  {_mode_cn(w.get('mode'))}"
            + (f"\n  · {why}" if why else "")
        )
    holds = []
    for h in positions:
        px = h.get("entry_px")
        try:
            px_s = f"{float(px):.2f}"
        except (TypeError, ValueError):
            px_s = "—"
        holds.append(f"● {h.get('name', '')} {h.get('code', '')}  {h.get('qty')}股 · 成本{px_s}")
    parts = [
        f"【短线复盘】{as_of} 收盘",
        f"【情绪】{_emotion_head(e, review=True)}\n{_emotion_bits(e)}",
        _section("严重异常波动", severe_lines(plan, horizon="次日")),
        _section("次日操作", op_lines, same_line=True),
        _section("观察", ["未入计划，不买", *obs[:12]]) if obs else "",
        _section("持仓", holds or ["— 空仓"]),
    ]
    return "\n\n".join(p for p in parts if p)


def _plan_state(p: dict, held: set[str]) -> str:
    code = str(p.get("code") or "")
    if code in held:
        return "持有"
    if p.get("buy"):
        return "待买"
    return "观察"


def intraday_body(
    *,
    as_of: str,
    now: str,
    e: Emotion,
    actions: list[str],
    plan: list[dict],
    positions: list[dict],
) -> str:
    held = {str(h.get("code") or "") for h in positions}
    plines = []
    for p in plan:
        state = _plan_state(p, held)
        mark = _mark(p.get("reasons") or [], buy=state == "待买")
        if state == "持有":
            mark = "●"
        extra = ""
        if state == "持有":
            extra = "\n  · 卖：约+10% / 冲高回落 / 跌停走"
        elif state == "待买":
            extra = "\n  · 14:30 后尾盘，涨停不追"
        else:
            why = "；".join(str(x) for x in (p.get("reasons") or [])[:2])
            extra = f"\n  · {why}" if why else "\n  · 不买"
        plines.append(f"{mark} {p.get('name', '')} {p.get('code', '')}  {state}{extra}")
    hlines = []
    for h in positions:
        try:
            px = f"{float(h.get('entry_px')):.2f}"
        except (TypeError, ValueError):
            px = "—"
        hlines.append(f"● {h.get('name', '')} {h.get('code', '')}  {h.get('qty')}股 · 成本{px}")
    parts = [
        f"【短线指令】{as_of} {now}",
        f"【情绪】{_emotion_head(e, review=False)}\n{_emotion_bits(e)}",
        _section("严重异常波动", severe_lines(plan, horizon="今日")),
        _section("执行", actions or ["— 本轮无新动作"]),
        _section("计划", plines or ["无"]),
        _section("持仓", hlines or ["— 空仓"]),
    ]
    return "\n\n".join(p for p in parts if p)
