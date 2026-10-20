"""盘后选股 + 盘中执行（半自动：决策与飞书，不对接券商）。"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from typing import Any

import pandas as pd

from quant.data.board_pools import read_board_pool
from quant.data.calendar import prev_trading_day, to_iso
from quant.data.zt_pool import read_zt_pool, read_zt_pool_range
from quant.tactic.bars import Bar, last_zt_streak, natural_break_index
from quant.tactic.capture import ensure_close_inputs, latest_trigger, load_bars
from quant.tactic.yidong import (
    settle_for_next_session,
    stamp_em_severe,
)
from quant.tactic.emotion import Emotion, evaluate_emotion
from quant.tactic.format import close_body, intraday_body
from quant.tactic.limits import close_limit_down, close_limit_up, limit_down_px, limit_up_px
from quant.tactic.mode_a import evaluate_mode_a
from quant.tactic.mode_b import evaluate_mode_b
from quant.tactic.state import INITIAL_CAPITAL, LIGHT_WEIGHT, MAX_PLAN, MAX_WEIGHT, load_state, save_state
from common.timeutil import cn_now


def _code6(s) -> str:
    t = str(s or "").strip()
    if t.endswith((".SZ", ".SH", ".BJ")):
        t = t.split(".")[0]
    digits = "".join(ch for ch in t if ch.isdigit())
    return digits.zfill(6) if digits else ""


def _codes_from_df(df) -> set[str]:
    if df is None or getattr(df, "empty", True):
        return set()
    for col in ("code", "代码", "股票代码"):
        if col in df.columns:
            return {_code6(x) for x in df[col].tolist() if _code6(x)}
    return set()


def _high_dt_n(dt, rec) -> int:
    dt_codes = _codes_from_df(dt)
    if not dt_codes or rec is None or getattr(rec, "empty", True):
        return 0
    hi = set(rec[rec["boards"] >= 5]["code"].astype(str).map(_code6))
    return int(len(hi & dt_codes))


def rank_watch(watch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """可买 A → 可买 B → 其余，同档按连板数。最多 3 只。"""

    def key(w: dict[str, Any]) -> tuple:
        if w.get("buy") and w.get("mode") == "A":
            tier = 0
        elif w.get("buy") and w.get("mode") == "B":
            tier = 1
        else:
            tier = 2
        return (tier, -int(w.get("boards") or 0), str(w.get("code") or ""))

    return sorted(watch, key=key)[:MAX_PLAN]


def _plan_weight(e: Emotion) -> float:
    if e.weight_scale < 1.0:
        return LIGHT_WEIGHT
    return MAX_WEIGHT


def _with_live_bar(
    bars: list[Bar], row: dict | None, as_of: str, code: str, name: str
) -> list[Bar]:
    if not row or not bars:
        return bars
    last = float(row.get("close") or 0)
    if last <= 0:
        return bars
    pre = float(row.get("pre_close") or bars[-1].close or 0)
    bar = Bar(
        date=as_of,
        open=float(row.get("open") or last),
        high=float(row.get("high") or last),
        low=float(row.get("low") or last),
        close=last,
        volume=0.0,
        prev_close=pre,
        name=name or bars[-1].name,
    )
    if bars[-1].date == as_of:
        return bars[:-1] + [bar]
    return bars + [bar]


def _bars_for(daily: pd.DataFrame, code: str, names: dict[str, str]) -> list[Bar]:
    g = daily[daily["code"].astype(str) == code].sort_values("date")
    out: list[Bar] = []
    prev = None
    for _, r in g.iterrows():
        close = float(r.get("close") or 0)
        pc = r.get("pre_close")
        try:
            pc = float(pc) if pc == pc and pc not in (None, "") else None
        except (TypeError, ValueError):
            pc = None
        if not pc or pc <= 0:
            pc = float(prev) if prev else 0.0
        out.append(
            Bar(
                date=str(r["date"])[:10],
                open=float(r.get("open") or 0),
                high=float(r.get("high") or 0),
                low=float(r.get("low") or 0),
                close=close,
                volume=float(r.get("volume") or 0),
                prev_close=float(pc or 0),
                name=names.get(code) or str(r.get("name") or ""),
            )
        )
        prev = close
    return out


def _promo(as_of: str, zt: pd.DataFrame) -> tuple[int, int]:
    prev_d = prev_trading_day(date.fromisoformat(as_of))
    if prev_d is None or zt is None or zt.empty or "boards" not in zt.columns:
        return 0, 0
    yzt = read_zt_pool(prev_d.isoformat())
    if yzt is None or yzt.empty or "boards" not in yzt.columns:
        return 0, 0
    y2 = yzt[pd.to_numeric(yzt["boards"], errors="coerce") == 2]
    n2 = int(len(y2))
    if not n2:
        return 0, 0
    codes = set(y2["code"].astype(str).map(_code6))
    t3 = zt[pd.to_numeric(zt["boards"], errors="coerce") >= 3]
    n23 = int(t3["code"].astype(str).map(_code6).isin(codes).sum()) if not t3.empty else 0
    return n2, n23


def _emotion_from_stores(as_of: str) -> Emotion:
    zt = read_zt_pool(as_of)
    zb = read_board_pool("zb", as_of)
    dt = read_board_pool("dt", as_of)
    prevp = read_board_pool("prev", as_of)
    if zt is None or zt.empty or zb is None or zb.empty or dt is None or dt.empty:
        raise RuntimeError(f"情绪池不完整 {as_of}")
    y_prem = None
    if prevp is not None and not prevp.empty and "pct" in prevp.columns:
        y_prem = float(pd.to_numeric(prevp["pct"], errors="coerce").mean())
    n2, n23 = _promo(as_of, zt)
    look = (date.fromisoformat(as_of) - timedelta(days=40)).isoformat()
    high_dt = _high_dt_n(dt, read_zt_pool_range(look, as_of))
    return evaluate_emotion(
        dt_n=len(dt),
        zt_n=len(zt),
        zb_n=len(zb),
        n_2board=n2,
        n_2to3=n23,
        y_zt_prem=y_prem,
        high_dt_n=high_dt,
    )


def _yidong_index(bars: list[Bar], yidong_date: str | None) -> int | None:
    if not yidong_date:
        return None
    day = str(yidong_date)[:10]
    for i, b in enumerate(bars):
        if b.date == day:
            return i
    return None


def _classify(
    bars: list[Bar],
    code: str,
    *,
    yidong_date: str | None = None,
) -> dict[str, Any]:
    n, start, end = last_zt_streak(bars, code, min_n=5)
    ongoing = end == len(bars) - 1 and n >= 5
    if n < 5:
        return {
            "mode": "drop",
            "boards": n,
            "buy": False,
            "fail": True,
            "reasons": ["不足5板"],
        }
    if ongoing:
        return {
            "mode": "observe",
            "boards": n,
            "buy": False,
            "fail": False,
            "reasons": ["仍在连板，只观察不买"],
        }
    last = bars[-1]
    if close_limit_up(last.close, last.prev_close, code, last.name):
        return {
            "mode": "observe",
            "boards": n,
            "buy": False,
            "fail": False,
            "reasons": ["收盘涨停，只观察不追"],
        }
    yi = _yidong_index(bars, yidong_date)
    if yi is not None and start >= 0 and yi >= start:
        if yi >= len(bars) - 1:
            return {
                "mode": "observe",
                "boards": n,
                "buy": False,
                "fail": False,
                "reasons": ["当日触发严重异常波动，不买"],
            }
        a = evaluate_mode_a(bars, code, yidong_i=yi)
        a["mode"] = "A"
        a["boards"] = n
        return a
    brk = natural_break_index(bars, code)
    if brk is not None:
        b = evaluate_mode_b(bars, code, break_i=brk)
        b["mode"] = "B"
        b["boards"] = n
        return b
    return {
        "mode": "observe",
        "boards": n,
        "buy": False,
        "fail": False,
        "reasons": ["控异动未触发或未断板，只观察"],
    }


def _focus_reasons(info: dict) -> list[str]:
    """计划行只留决定性理由。涨停、连板、严重异动优先于横盘缩量。"""
    raw = [str(r) for r in (info.get("reasons") or []) if str(r).strip()]
    out: list[str] = []
    for r in raw:
        if any(k in r for k in ("涨停", "连板", "停牌")):
            out.append(r)
    if info.get("severe_today") or info.get("severe_state") == "hit":
        why = str(info.get("severe_why") or "严重异常波动")
        line = f"已触发{why}，规避观察"
        if line not in out:
            out.append(line)
    elif info.get("severe_next_ret") is not None and not out:
        ret = float(info["severe_next_ret"])
        why = str(info.get("severe_next_why") or "严重异常波动")
        verb = "小于" if ret < 0 else "达到"
        out.append(f"规避严重异动，收盘涨幅{verb}{ret:+.2%}触发{why}")
    if out:
        return out
    return raw[:2]


def _em_severe_rows() -> list[dict]:
    from quant.data.sources.interface import try_with_fallback

    rows = try_with_fallback("market", "fetch_severe_anomaly")
    return list(rows or [])


def run_close(as_of: str, *, push: bool = True) -> dict[str, Any]:
    as_of = to_iso(as_of)
    st = load_state()
    extra: set[str] = set()
    for w in list(st.get("watch") or []) + list(st.get("positions") or []):
        c = _code6(w.get("code"))
        if c:
            extra.add(c)
    codes = ensure_close_inputs(as_of, extra)
    em_rows = _em_severe_rows()
    e = _emotion_from_stores(as_of)
    old = {_code6(w.get("code")): w for w in (st.get("watch") or [])}

    watch: list[dict[str, Any]] = []
    for code in sorted(codes):
        frame = load_bars(code)
        bars = _bars_for(frame, code, {}) if frame is not None and not frame.empty else []
        if not bars:
            if old.get(code):
                watch.append(old[code])
            continue
        info = _classify(bars, code, yidong_date=latest_trigger(code, as_of=as_of))
        if info.get("mode") == "drop":
            continue
        nm = bars[-1].name or code
        halted = str(bars[-1].date) < as_of
        info = stamp_em_severe(info, em_rows, code, session="next")
        info = settle_for_next_session(
            info,
            hit_why=str(info.get("severe_why") or "") if info.get("severe_today") else "",
            halted=halted,
        )
        info["reasons"] = _focus_reasons(info)
        if any("规避" in str(r) for r in info["reasons"]):
            info["mode"] = "observe"
            info["buy"] = False
        if info.get("fail"):
            continue
        watch.append(
            {
                "code": code,
                "name": nm,
                "mode": info.get("mode") or "observe",
                "boards": int(info.get("boards") or 0),
                "buy": bool(info.get("buy")),
                "reasons": list(info.get("reasons") or []),
                "severe_today": bool(info.get("severe_today")),
                "severe_why": info.get("severe_why") or "",
                "severe_state": info.get("severe_state") or "",
                "severe_next_ret": info.get("severe_next_ret"),
                "severe_next_why": info.get("severe_next_why") or "",
                "severe_days": info.get("severe_days"),
                "severe_dev": info.get("severe_dev"),
            }
        )

    watch = rank_watch(watch)
    wgt = _plan_weight(e)
    if e.new_entry == "block":
        for w in watch:
            w["buy"] = False
        plan = [
            {
                **w,
                "buy": False,
                "weight": 0.0,
                "sell_rule": "不买",
                "when": "观察",
            }
            for w in watch
        ]
    else:
        plan = []
        for w in watch:
            buy = bool(w.get("buy") and w.get("mode") in ("A", "B"))
            plan.append(
                {
                    **w,
                    "buy": buy,
                    "weight": wgt if buy else 0.0,
                    "sell_rule": (
                        "约+10%或冲高回落；跌停走"
                        if buy
                        else "不买"
                    ),
                    "when": "尾盘" if buy else "观察",
                }
            )

    st["as_of"] = as_of
    st["watch"] = watch
    st["plan"] = plan
    st["emotion"] = {
        "new_entry": e.new_entry,
        "dt_n": e.dt_n,
        "zb_rate": e.zb_rate,
        "promo_2to3": e.promo_2to3,
        "y_zt_prem": e.y_zt_prem,
        "reasons": e.reasons,
    }
    nxt = None
    from quant.data.calendar import next_trading_day

    nxt = next_trading_day(date.fromisoformat(as_of))
    st["plan_for"] = nxt.isoformat() if nxt else as_of
    save_state(st)
    body = close_body(
        as_of=as_of,
        e=e,
        watch=watch,
        plan=plan,
        positions=st.get("positions") or [],
        plan_for=st["plan_for"],
    )
    from quant.ops.push import push_text

    push_text("短线复盘", body, mode="tactic_close", push=push, raw=True)
    try:
        from quant.tactic.ml_archive import codes_of_day, record_day

        ml_codes, ml_names = codes_of_day(as_of, watch, st.get("positions") or [])
        record_day(as_of, ml_codes, ml_names)
    except Exception as exc:
        print(f"[ml] 归档跳过 {as_of}: {exc}", flush=True)
    return {"as_of": as_of, "emotion": e, "watch": watch, "plan": plan, "body": body}


def _spot_map(spot: pd.DataFrame) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if spot is None or spot.empty:
        return out
    for r in spot.to_dict("records"):
        c = _code6(r.get("code"))
        if c:
            out[c] = r
    return out


def _spot_halted(row: dict | None) -> bool:
    """无成交且价格无波动，视为停牌。缺行情不当停牌。"""
    if not row:
        return False
    try:
        vol = float(row.get("volume") or 0)
    except (TypeError, ValueError):
        vol = 0.0
    last = float(row.get("close") or 0)
    pre = float(row.get("pre_close") or 0)
    high = float(row.get("high") or last)
    low = float(row.get("low") or last)
    return vol <= 0 and last > 0 and abs(high - low) <= 0.001 and abs(last - pre) <= 0.001


def _lot_qty(price: float, weight: float, equity: float) -> int:
    if price <= 0:
        return 0
    raw = equity * weight / price
    return int(raw // 100) * 100


def _book_frame(codes: set[str]) -> pd.DataFrame:
    frames = []
    for code in codes:
        df = load_bars(code)
        if df is None or df.empty:
            continue
        d = df.copy()
        d["code"] = code
        frames.append(d)
    if not frames:
        return pd.DataFrame(columns=["code", "date"])
    return pd.concat(frames, ignore_index=True)


def _live_emotion(as_of: str) -> Emotion:
    try:
        from quant.data.sources.interface import try_with_fallback

        zt = try_with_fallback("market", "fetch_zt_pool_hist", date=as_of)
        zb = try_with_fallback("market", "fetch_zt_zb_pool", date=as_of)
        dt = try_with_fallback("market", "fetch_zt_dt_pool", date=as_of)
        prevp = try_with_fallback("market", "fetch_zt_prev_pool", date=as_of)
        if zt is None or getattr(zt, "empty", True):
            raise RuntimeError("涨停池空")
        y_prem = None
        if prevp is not None and not getattr(prevp, "empty", True):
            col = "涨跌幅" if "涨跌幅" in prevp.columns else "pct"
            if col in prevp.columns:
                y_prem = float(pd.to_numeric(prevp[col], errors="coerce").mean())
        bcol = "连板数" if "连板数" in zt.columns else "boards"
        tcol = "代码" if "代码" in zt.columns else "code"
        work = zt.copy()
        work["code"] = work[tcol].map(_code6)
        work["boards"] = pd.to_numeric(work[bcol], errors="coerce") if bcol in work.columns else 0
        n2, n23 = _promo(as_of, work)
        look = (date.fromisoformat(as_of) - timedelta(days=40)).isoformat()
        rec = read_zt_pool_range(look, as_of)
        high_dt = _high_dt_n(dt, rec if rec is not None and not getattr(rec, "empty", True) else work)
        zb_n = 0 if zb is None or getattr(zb, "empty", True) else len(zb)
        dt_n = 0 if dt is None or getattr(dt, "empty", True) else len(dt)
        return evaluate_emotion(
            dt_n=dt_n,
            zt_n=len(zt),
            zb_n=zb_n,
            n_2board=n2,
            n_2to3=n23,
            y_zt_prem=y_prem,
            high_dt_n=high_dt,
        )
    except Exception:
        return _emotion_from_stores(as_of)


def run_intraday(*, push: bool = True, now=None) -> dict[str, Any]:
    from quant.data.fetch import fetch_spot

    ref = now or cn_now()
    as_of = ref.date().isoformat()
    st = load_state()
    plan_for = str(st.get("plan_for") or st.get("as_of") or "")
    codes = {_code6(x.get("code")) for x in list(st.get("plan") or []) + list(st.get("positions") or [])}
    codes.discard("")
    daily = _book_frame(codes)
    e = _live_emotion(as_of)
    spot = fetch_spot()
    smap = _spot_map(spot)
    actions: list[str] = []
    positions: list[dict] = list(st.get("positions") or [])
    pending = list(st.get("pending_auction_exits") or [])
    cash = float(st.get("cash") or INITIAL_CAPITAL)
    equity = cash + sum(
        float(h.get("qty") or 0) * float((smap.get(h["code"]) or {}).get("close") or h.get("entry_px") or 0)
        for h in positions
    )

    hhmm = ref.hour * 100 + ref.minute
    tail = hhmm >= 1430
    auction = hhmm < 950

    # 次日竞价处理隔夜跌停未成交
    if auction and pending:
        still = []
        for code in pending:
            row = smap.get(code)
            if not row:
                still.append(code)
                continue
            last = float(row.get("close") or row.get("open") or 0)
            pos = next((p for p in positions if p["code"] == code), None)
            if pos is None:
                continue
            cash += float(pos["qty"]) * last
            positions = [p for p in positions if p["code"] != code]
            actions.append(f"卖出  {pos.get('name') or code} {code}  {pos.get('qty')}股  {last:.2f}  竞价（昨跌停未走掉）")
        pending = still

    new_pos: list[dict] = []
    for h in positions:
        code = h["code"]
        row = smap.get(code)
        if not row:
            new_pos.append(h)
            continue
        last = float(row.get("close") or 0)
        high = float(row.get("high") or last)
        pre = float(row.get("pre_close") or h.get("entry_px") or last)
        name = h.get("name") or code
        if close_limit_down(last, pre, code, name):
            if last <= limit_down_px(pre, code, name) + 0.005 and float(row.get("low") or last) >= last - 0.005:
                pending.append(code)
                new_pos.append(h)
                actions.append(f"{name} {code}  跌停封死，记次日竞价走")
                continue
            cash += float(h["qty"]) * last
            actions.append(f"卖出  {name} {code}  {h.get('qty')}股  {last:.2f}  跌停")
            continue
        entry = float(h.get("entry_px") or last)
        if last >= entry * 1.10:
            cash += float(h["qty"]) * last
            actions.append(f"卖出  {name} {code}  {h.get('qty')}股  {last:.2f}  约+10%")
            continue
        if pre > 0 and high / pre - 1.0 >= 0.05 and last <= high * 0.97:
            cash += float(h["qty"]) * last
            actions.append(f"卖出  {name} {code}  {h.get('qty')}股  {last:.2f}  冲高回落")
            continue
        if h.get("mode") == "B" and str(h.get("entry_date")) < as_of and last > entry:
            cash += float(h["qty"]) * last
            actions.append(f"卖出  {name} {code}  {h.get('qty')}股  {last:.2f}  次日冲高")
            continue
        new_pos.append(h)
    positions = new_pos

    held = {p["code"] for p in positions}
    plan = list(st.get("plan") or [])
    em_rows = _em_severe_rows()
    for p in plan:
        code = _code6(p.get("code"))
        if not code:
            continue
        stamped = stamp_em_severe(p, em_rows, code, session="today")
        p["severe_next_ret"] = stamped.get("severe_next_ret")
        p["severe_next_why"] = stamped.get("severe_next_why") or ""
        p["severe_days"] = stamped.get("severe_days")
        p["severe_dev"] = stamped.get("severe_dev")
        p["severe_today"] = bool(stamped.get("severe_today"))
        p["severe_why"] = stamped.get("severe_why") or ""
        row = smap.get(code)
        halted = _spot_halted(row)
        name = p.get("name") or code
        if row:
            last = float(row.get("close") or 0)
            pre = float(row.get("pre_close") or 0)
            if close_limit_up(last, pre, code, name):
                p["mode"] = "observe"
                p["buy"] = False
                p["reasons"] = ["今日涨停，只观察不追"]
        if halted:
            p["mode"] = "observe"
            p["buy"] = False
            p["severe_state"] = "halt"
            p["reasons"] = ["停牌，观察"]
            actions.append(f"⏸ {name} {code}  停牌，转观察")
        elif p.get("severe_today"):
            p["mode"] = "observe"
            p["buy"] = False
            p["severe_state"] = "hit"
            actions.append(f"⚠ {name} {code}  已触发{p.get('severe_why') or ''}，转观察")
        else:
            p["severe_state"] = p.get("severe_state") or ""
        p["reasons"] = _focus_reasons(p)
        if any("规避" in str(r) or "涨停" in str(r) for r in p["reasons"]):
            p["mode"] = "observe"
            p["buy"] = False
    if plan_for == as_of and e.allow_new and tail:
        for p in plan:
            if not p.get("buy"):
                continue
            code = p["code"]
            if code in held:
                continue
            if e.plan_only and not p.get("buy"):
                continue
            row = smap.get(code)
            name = p.get("name") or code
            if not row:
                actions.append(f"未成  {name} {code}  无行情")
                continue
            last = float(row.get("close") or 0)
            pre = float(row.get("pre_close") or 0)
            if close_limit_up(last, pre, code, name):
                actions.append(f"未成  {name} {code}  已涨停，不追")
                continue
            live_bars = _with_live_bar(_bars_for(daily, code, {}), row, as_of, code, name)
            info = (
                _classify(live_bars, code, yidong_date=latest_trigger(code, as_of=as_of))
                if live_bars
                else {}
            )
            if info.get("fail") or not info.get("buy"):
                why = "；".join((info.get("reasons") or ["盘中重判不买"])[:2])
                actions.append(f"未成  {name} {code}  {why}")
                continue
            w = min(
                MAX_WEIGHT * float(e.weight_scale or 1.0),
                float(p.get("weight") or MAX_WEIGHT),
            )
            qty = _lot_qty(last, w, equity)
            cost = qty * last
            if qty < 100 or cost > cash:
                actions.append(f"未成  {name} {code}  资金不足")
                continue
            cash -= cost
            positions.append(
                {
                    "code": code,
                    "name": name,
                    "mode": p.get("mode"),
                    "qty": qty,
                    "entry_px": last,
                    "entry_date": as_of,
                    "weight": w,
                }
            )
            held.add(code)
            actions.append(f"买入  {name} {code}  {qty}股  {last:.2f}  仓位{int(round(w * 100))}%")

    st["positions"] = positions
    st["pending_auction_exits"] = pending
    st["cash"] = round(cash, 2)
    body = intraday_body(
        as_of=as_of,
        now=ref.strftime("%H:%M"),
        e=e,
        actions=actions,
        plan=plan,
        positions=positions,
    )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    changed = digest != st.get("last_hash")
    st["last_hash"] = digest
    save_state(st)
    do_push = push and (changed or any("买入" in a or "卖出" in a for a in actions))
    if do_push:
        from quant.ops.push import push_text

        push_text("短线指令", body, mode="tactic_intraday", push=True, raw=True)
    return {"as_of": as_of, "actions": actions, "pushed": do_push, "body": body, "emotion": e}
