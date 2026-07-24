"""信号持续确认：当日锁存 + 累计命中，第 N 次仍满足时成交。

买入/卖出：首次触发当日锁存至收盘（跨日清）；累计命中 min_day_hits 次，
且自首次触发起 min_span_minutes 已满足，且当前轮条件仍成立 → 可执行。
买入/卖出成交前再验；趋势类卖仍须 14:30 后执行。

持久化：~/.quant/state/signal_pending.json
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime

from quant.constants import BUY_KIND_ASCENT, BUY_KIND_PULLBACK
from quant.timeutil import CN_TZ, cn_date_str, cn_now, ensure_cn_tz, trading_minutes_between
from quant.config import load_gates_config
from quant.scoring.context import ScoreContext, infer_regime
from quant.trading.models import TradeSignal
from quant.store.paths import state_file
from quant.store.state import resolve_payload_holdings
from quant.trading.sell_policy import sell_requires_late_session
from quant.trading_hours import is_late_session_for_trend_sell

_PENDING_FILE = "signal_pending.json"
_BUY_KINDS = {BUY_KIND_ASCENT, BUY_KIND_PULLBACK, "默认"}


@dataclass
class PendingSignal:
    code: str
    action: str
    signal_kind: str
    count: int  # 当日累计命中次数（不必连续）
    first_at: str
    last_at: str
    regime: str
    name: str = ""
    last_reason: str = ""
    miss_streak: int = 0  # 连续缺轮（仅统计，日锁存下不清 pending）
    first_date: str = ""  # 首次满足的自然日 yyyy-mm-dd

    def key(self) -> str:
        return f"{self.code}|{self.action}|{self.signal_kind}"


def _now() -> datetime:
    return cn_now()


def _parse_ts(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CN_TZ)
        return ensure_cn_tz(dt)
    except ValueError:
        return None


def _entry_from_dict(v: dict) -> PendingSignal | None:
    try:
        return PendingSignal(
            code=str(v["code"]),
            action=str(v["action"]),
            signal_kind=str(v["signal_kind"]),
            count=int(v["count"]),
            first_at=str(v["first_at"]),
            last_at=str(v["last_at"]),
            regime=str(v.get("regime") or ""),
            name=str(v.get("name") or ""),
            last_reason=str(v.get("last_reason") or ""),
            miss_streak=int(v.get("miss_streak") or 0),
            first_date=str(v.get("first_date") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def confirmation_config(ctx: ScoreContext, signal_kind: str = "", *, action: str = "") -> dict:
    """按 signal_kind → 市场状态 → 全局默认 解析持续确认参数。"""
    cfg = load_gates_config().get("confirmation") or {}
    regime = infer_regime(ctx.payload)
    regime_block = cfg.get(regime) or cfg.get("震荡") or {}
    kind_block = (cfg.get("by_kind") or {}).get(signal_kind) or {}
    day_cfg = cfg.get("day_latch") or {}

    min_span = kind_block.get("min_span_minutes")
    if min_span is None:
        min_span = kind_block.get("persistence_minutes")
    if min_span is None:
        min_span = regime_block.get("min_span_minutes")
    if min_span is None:
        min_span = regime_block.get("persistence_minutes")
    if min_span is None:
        min_span = cfg.get("default_min_span_minutes")
    if min_span is None:
        min_span = cfg.get("default_persistence_minutes", 10)

    min_hits = kind_block.get("min_day_hits")
    if min_hits is None:
        min_hits = kind_block.get("min_consecutive_runs")
    if min_hits is None:
        min_hits = regime_block.get("min_day_hits")
    if min_hits is None:
        min_hits = regime_block.get("min_consecutive_runs")
    if min_hits is None:
        min_hits = cfg.get("default_min_day_hits")
    if min_hits is None:
        min_hits = cfg.get("default_min_consecutive_runs", 2)

    min_span_f = float(min_span)
    min_hits_i = max(1, int(min_hits))
    if min_span_f <= 0:
        min_hits_i = max(1, min_hits_i)

    max_window = kind_block.get("max_window_minutes")
    if max_window is None:
        max_window = regime_block.get("max_window_minutes")
    if max_window is None:
        max_window = cfg.get("max_window_minutes", 180)

    multi_day = bool(kind_block.get("multi_day", False))
    day_latch = bool(day_cfg.get("enabled", True)) and not multi_day

    out = {
        "min_span_minutes": min_span_f,
        "min_day_hits": min_hits_i,
        "persistence_minutes": min_span_f,
        "min_consecutive_runs": min_hits_i,
        "max_window_minutes": float(max_window),
        "regime": regime,
        "day_latch": day_latch,
        "multi_day": multi_day,
        "same_day_window": day_latch,
        "purge_on_miss": not day_latch,
        "max_miss_streak": 0,
        "use_trading_minutes": False,
        "verify_before_execute": False,
    }

    is_buy = action == "买入" or signal_kind in _BUY_KINDS
    if is_buy and action != "卖出":
        buy_pol = cfg.get("buy_policy") or {}
        if day_latch:
            out["use_trading_minutes"] = bool(buy_pol.get("use_trading_minutes", True))
        else:
            out["same_day_window"] = bool(buy_pol.get("same_day_window", True))
            out["max_miss_streak"] = int(buy_pol.get("max_miss_streak", 1))
            out["use_trading_minutes"] = bool(buy_pol.get("use_trading_minutes", True))
        out["verify_before_execute"] = bool(buy_pol.get("verify_before_execute", True))
    elif action == "卖出":
        sell_pol = cfg.get("sell_policy") or {}
        out["verify_before_execute"] = bool(sell_pol.get("verify_before_execute", True))

    return out


def persistence_satisfied(
    entry: PendingSignal,
    now: datetime,
    *,
    persistence_minutes: float | None = None,
    min_consecutive_runs: int | None = None,
    min_span_minutes: float | None = None,
    min_day_hits: int | None = None,
    use_trading_minutes: bool = False,
) -> bool:
    """累计命中 + 自首次触发起最短间隔（当前轮须仍满足，由调用方保证）。"""
    span = min_span_minutes if min_span_minutes is not None else (persistence_minutes or 0)
    hits = min_day_hits if min_day_hits is not None else (min_consecutive_runs or 1)
    if entry.count < hits:
        return False
    if span <= 0:
        return True
    first_dt = _parse_ts(entry.first_at)
    if first_dt is None:
        return False
    if use_trading_minutes:
        elapsed = trading_minutes_between(first_dt, now)
    else:
        elapsed = (now - first_dt).total_seconds() / 60.0
    return elapsed >= span


def load_pending() -> dict[str, PendingSignal]:
    path = state_file(_PENDING_FILE)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, PendingSignal] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if isinstance(v, dict):
            entry = _entry_from_dict(v)
            if entry is not None:
                out[k] = entry
    return out


def save_pending(pending: dict[str, PendingSignal]) -> None:
    path = state_file(_PENDING_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: asdict(v) for k, v in pending.items()}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _purge_stale_pending(
    pending: dict[str, PendingSignal],
    today: str,
    now: datetime,
    ctx: ScoreContext,
) -> None:
    """跨日清日锁存；多日窗口类按 max_window 清；卖出确认须仍持仓。"""
    held = {
        str(h.get("股票代码", "")).strip()
        for h in resolve_payload_holdings(ctx.payload)
        if isinstance(h, dict)
    }
    for key in list(pending.keys()):
        entry = pending[key]
        if entry.action == "卖出" and entry.code not in held:
            del pending[key]
            continue
        conf = confirmation_config(ctx, entry.signal_kind, action=entry.action)
        if conf.get("day_latch"):
            if entry.first_date and entry.first_date != today:
                del pending[key]
            continue
        if conf.get("same_day_window") and entry.action == "买入":
            if entry.first_date and entry.first_date != today:
                del pending[key]
            continue
        elapsed = _elapsed_minutes(entry, now, use_trading_minutes=conf.get("use_trading_minutes", False))
        if elapsed > conf["max_window_minutes"]:
            del pending[key]


def _needs_late_final_confirm(sig: TradeSignal, ctx: ScoreContext | None = None) -> bool:
    if sig.action != "卖出":
        return False
    stock = None
    code = sig.code
    if ctx is not None:
        for key in ("持仓股", "自选股"):
            for row in ctx.payload.get(key) or []:
                if str(row.get("股票代码", "")).strip() == code:
                    stock = row
                    break
            if stock:
                break
    return sell_requires_late_session(sig, stock, code)


def _waiting_late_session(
    entry: PendingSignal,
    sig: TradeSignal,
    now: datetime,
    conf: dict,
    ctx: ScoreContext | None = None,
) -> bool:
    """累计确认已达标，但非紧急卖须等 14:30 后成交。"""
    if not _needs_late_final_confirm(sig, ctx):
        return False
    if is_late_session_for_trend_sell(now):
        return False
    return persistence_satisfied(
        entry,
        now,
        min_span_minutes=conf["min_span_minutes"],
        min_day_hits=conf["min_day_hits"],
        use_trading_minutes=conf.get("use_trading_minutes", False),
    )


def _clear_opposite_pending(raw_signals: list[TradeSignal]) -> None:
    """同代码出现反向原始信号时，清除对向持续确认进度。"""
    if not raw_signals:
        return
    pending = load_pending()
    changed = False
    for sig in raw_signals:
        opp = "买入" if sig.action == "卖出" else "卖出"
        for key in list(pending.keys()):
            entry = pending[key]
            if entry.code == sig.code and entry.action == opp:
                del pending[key]
                changed = True
    if changed:
        save_pending(pending)


def _elapsed_minutes(entry: PendingSignal, now: datetime, *, use_trading_minutes: bool) -> float:
    first_dt = _parse_ts(entry.first_at)
    if first_dt is None:
        return 0.0
    if use_trading_minutes:
        return trading_minutes_between(first_dt, now)
    return (now - first_dt).total_seconds() / 60.0


def _new_pending(sig: TradeSignal, kind: str, regime: str, now: datetime) -> PendingSignal:
    return PendingSignal(
        code=sig.code,
        action=sig.action,
        signal_kind=kind,
        count=1,
        first_at=now.isoformat(),
        last_at=now.isoformat(),
        regime=regime,
        name=sig.name,
        last_reason=sig.reason,
        miss_streak=0,
        first_date=cn_date_str(now),
    )


def _should_reset_window(
    entry: PendingSignal,
    conf: dict,
    elapsed: float,
    *,
    waiting_late: bool,
    today: str,
) -> bool:
    if waiting_late:
        return False
    if conf.get("day_latch"):
        return entry.first_date != today if entry.first_date else False
    if conf.get("same_day_window") and entry.action == "买入":
        if entry.first_date and entry.first_date != today:
            return True
        return False
    return elapsed > conf["max_window_minutes"]


def _pending_status(entry: PendingSignal, conf: dict, elapsed: float) -> str:
    span = conf["min_span_minutes"]
    hits = conf["min_day_hits"]
    miss_note = f"，缺轮{entry.miss_streak}" if entry.miss_streak else ""
    if conf.get("day_latch"):
        return (
            f"当日锁存确认中（累计{entry.count}/{hits}次，"
            f"{elapsed:.0f}/{span:.0f}分{miss_note}）"
        )
    return f"持续确认中（{elapsed:.0f}/{span:.0f}分，{entry.count}/{hits}次）"


def _try_execute(
    sig: TradeSignal,
    entry: PendingSignal,
    conf: dict,
    now: datetime,
    ctx: ScoreContext | None = None,
) -> tuple[TradeSignal | None, str]:
    """累计确认达标后生成可执行信号；返回 (exec_sig, audit_status)。"""
    kind = sig.signal_kind or "默认"
    span = conf["min_span_minutes"]
    hits = conf["min_day_hits"]
    use_tm = conf.get("use_trading_minutes", False)
    elapsed = _elapsed_minutes(entry, now, use_trading_minutes=use_tm)

    if not persistence_satisfied(
        entry,
        now,
        min_span_minutes=span,
        min_day_hits=hits,
        use_trading_minutes=use_tm,
    ):
        return None, _pending_status(entry, conf, elapsed)

    if _waiting_late_session(entry, sig, now, conf, ctx):
        return None, "累计确认已达标，等待14:30后执行"

    if sig.action == "买入" and conf.get("verify_before_execute") and ctx is not None:
        from quant.signals.verify import verify_buy_signal_still_valid

        if not verify_buy_signal_still_valid(
            sig.code,
            ctx,
            mode=ctx.mode or "during_market",
            signal_kind=kind,
        ):
            return None, "累计确认已达标但买点已失效，暂不成交"

    if sig.action == "卖出" and conf.get("verify_before_execute") and ctx is not None:
        from quant.signals.verify import verify_sell_signal_still_valid

        if not verify_sell_signal_still_valid(sig.code, ctx, signal_kind=kind):
            return None, "累计确认已达标但卖点已失效，暂不成交"

    exec_sig = TradeSignal(
        action=sig.action,
        code=sig.code,
        name=sig.name,
        price=sig.price,
        quantity=sig.quantity,
        strategy=sig.strategy,
        reason=(
            f"累计确认完成（{entry.count}次/{hits}次，{elapsed:.0f}分/{span:.0f}分）；"
            f"{sig.reason}"
        ),
        sell_type=sig.sell_type,
        signal_kind=kind,
        confirmation_stage=entry.count,
    )
    status = "累计确认完成，可交易"
    if _needs_late_final_confirm(sig):
        status = "累计确认完成（14:30后），可交易"
    return exec_sig, status


def _audit_row(
    sig: TradeSignal | None,
    entry: PendingSignal,
    status: str,
    *,
    executable: bool,
) -> dict:
    return {
        "股票代码": entry.code,
        "股票名称": (sig.name if sig else "") or entry.name or entry.code,
        "方向": entry.action,
        "信号类型": entry.signal_kind,
        "确认次数": entry.count,
        "状态": status,
        "可执行": executable,
        "理由": (sig.reason if sig else "") or entry.last_reason,
    }


def _append_latched_pending_audit(
    pending: dict[str, PendingSignal],
    audit: list[dict],
    ctx: ScoreContext,
    now: datetime,
    *,
    scope_actions: set[str],
    seen_keys: set[str],
) -> None:
    """为当日锁存但本轮缺信号的 pending 补审计行（供推送展示）。"""
    audited = {(r.get("股票代码"), r.get("方向")) for r in audit}
    for key, entry in pending.items():
        if key in seen_keys:
            continue
        if scope_actions and entry.action not in scope_actions:
            continue
        if (entry.code, entry.action) in audited:
            continue
        conf = confirmation_config(ctx, entry.signal_kind, action=entry.action)
        if not conf.get("day_latch"):
            continue
        use_tm = conf.get("use_trading_minutes", False)
        elapsed = _elapsed_minutes(entry, now, use_trading_minutes=use_tm)
        audit.append(
            _audit_row(
                None,
                entry,
                _pending_status(entry, conf, elapsed),
                executable=False,
            )
        )


def apply_three_confirmations(
    raw_signals: list[TradeSignal],
    ctx: ScoreContext,
    *,
    scope_action: str | None = None,
) -> tuple[list[TradeSignal], list[dict]]:
    """持续确认：返回可执行信号 + 审计日志。"""
    _clear_opposite_pending(raw_signals)
    now = _now()
    today = cn_date_str(now)
    pending = load_pending()
    _purge_stale_pending(pending, today, now, ctx)
    executable: list[TradeSignal] = []
    audit: list[dict] = []
    seen_keys: set[str] = set()
    if scope_action:
        scope_actions = {scope_action}
    elif raw_signals:
        scope_actions = {sig.action for sig in raw_signals}
    else:
        scope_actions = set()

    for sig in raw_signals:
        kind = sig.signal_kind or "默认"
        key = f"{sig.code}|{sig.action}|{kind}"
        seen_keys.add(key)
        conf = confirmation_config(ctx, kind, action=sig.action)
        regime = conf["regime"]
        entry = pending.get(key)

        if entry is None:
            entry = _new_pending(sig, kind, regime, now)
            pending[key] = entry
            exec_sig, status = _try_execute(sig, entry, conf, now, ctx)
            if exec_sig:
                executable.append(exec_sig)
                del pending[key]
                audit.append(_audit_row(sig, entry, status, executable=True))
            else:
                audit.append(_audit_row(sig, entry, status, executable=False))
            continue

        first_dt = _parse_ts(entry.first_at)
        if first_dt is None:
            entry = _new_pending(sig, kind, regime, now)
            pending[key] = entry
            audit.append(_audit_row(sig, entry, "时间戳异常，重新计时", executable=False))
            continue

        use_tm = conf.get("use_trading_minutes", False)
        elapsed = _elapsed_minutes(entry, now, use_trading_minutes=use_tm)
        waiting_late = _waiting_late_session(entry, sig, now, conf, ctx)

        if _should_reset_window(entry, conf, elapsed, waiting_late=waiting_late, today=today):
            reset_reason = (
                "跨日重新计时"
                if entry.first_date and entry.first_date != today
                else "持续超时，重新计时"
            )
            entry = _new_pending(sig, kind, regime, now)
            pending[key] = entry
            audit.append(_audit_row(sig, entry, reset_reason, executable=False))
            continue

        entry.miss_streak = 0
        entry.count += 1
        entry.last_at = now.isoformat()
        entry.last_reason = sig.reason
        entry.regime = regime
        entry.name = sig.name or entry.name
        if not entry.first_date:
            entry.first_date = today
        pending[key] = entry

        exec_sig, status = _try_execute(sig, entry, conf, now, ctx)
        if exec_sig:
            executable.append(exec_sig)
            del pending[key]
            audit.append(_audit_row(sig, entry, status, executable=True))
        else:
            audit.append(_audit_row(sig, entry, status, executable=False))

    for key in list(pending.keys()):
        entry = pending[key]
        if entry.action not in scope_actions or key in seen_keys:
            continue
        conf = confirmation_config(ctx, entry.signal_kind, action=entry.action)
        if not conf.get("purge_on_miss", True):
            entry.miss_streak += 1
            pending[key] = entry
            continue
        max_miss = int(conf.get("max_miss_streak", 0))
        if max_miss > 0 and entry.miss_streak < max_miss:
            entry.miss_streak += 1
            pending[key] = entry
        else:
            del pending[key]

    _append_latched_pending_audit(
        pending,
        audit,
        ctx,
        now,
        scope_actions=scope_actions,
        seen_keys=seen_keys,
    )

    save_pending(pending)
    return executable, audit
