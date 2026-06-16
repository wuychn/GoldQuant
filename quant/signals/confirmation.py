"""信号持续确认：条件须保持足够时长与调度轮次，方可成交。

买入：当日有效（跨日清零）、午休不计中断、允许 1 轮软中断、成交前再验买点。
卖出：条件消失仍清零；趋势类卖须 14:30 后执行。

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
from quant.signals.models import TradeSignal
from quant.store.paths import state_file
from quant.trading_hours import is_late_session_for_trend_sell, sell_kinds_requiring_late_final

_PENDING_FILE = "signal_pending.json"
_BUY_KINDS = {BUY_KIND_ASCENT, BUY_KIND_PULLBACK, "默认"}


@dataclass
class PendingSignal:
    code: str
    action: str
    signal_kind: str
    count: int  # 累计满足条件的调度轮次
    first_at: str
    last_at: str
    regime: str
    last_reason: str = ""
    miss_streak: int = 0  # 连续未出现轮次（买入软中断）
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

    persist = kind_block.get("persistence_minutes")
    if persist is None:
        persist = regime_block.get("persistence_minutes")
    if persist is None:
        persist = cfg.get("default_persistence_minutes", 10)

    min_runs = kind_block.get("min_consecutive_runs")
    if min_runs is None:
        min_runs = regime_block.get("min_consecutive_runs")
    if min_runs is None:
        min_runs = cfg.get("default_min_consecutive_runs", 2)
    persist_f = float(persist)
    min_runs_i = max(1, int(min_runs))
    if persist_f <= 0:
        min_runs_i = 1

    max_window = kind_block.get("max_window_minutes")
    if max_window is None:
        max_window = regime_block.get("max_window_minutes")
    if max_window is None:
        max_window = cfg.get("max_window_minutes", 180)

    out = {
        "persistence_minutes": persist_f,
        "min_consecutive_runs": min_runs_i,
        "max_window_minutes": float(max_window),
        "regime": regime,
        "same_day_window": False,
        "max_miss_streak": 0,
        "use_trading_minutes": False,
        "verify_before_execute": False,
    }

    is_buy = action == "买入" or signal_kind in _BUY_KINDS
    if is_buy and action != "卖出":
        buy_pol = cfg.get("buy_policy") or {}
        out["same_day_window"] = bool(buy_pol.get("same_day_window", True))
        out["max_miss_streak"] = int(buy_pol.get("max_miss_streak", 1))
        out["use_trading_minutes"] = bool(buy_pol.get("use_trading_minutes", True))
        out["verify_before_execute"] = bool(buy_pol.get("verify_before_execute", True))

    return out


def persistence_satisfied(
    entry: PendingSignal,
    now: datetime,
    *,
    persistence_minutes: float,
    min_consecutive_runs: int,
    use_trading_minutes: bool = False,
) -> bool:
    """是否达到持续时长 + 轮次（买入可用连续竞价分钟，剔除午休）。"""
    first_dt = _parse_ts(entry.first_at)
    if first_dt is None:
        return False
    if persistence_minutes <= 0:
        return entry.count >= min_consecutive_runs
    if use_trading_minutes:
        elapsed = trading_minutes_between(first_dt, now)
    else:
        elapsed = (now - first_dt).total_seconds() / 60.0
    return elapsed >= persistence_minutes and entry.count >= min_consecutive_runs


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


def _purge_cross_day_buy_pending(pending: dict[str, PendingSignal], today: str) -> None:
    for key in list(pending.keys()):
        entry = pending[key]
        if entry.action != "买入":
            continue
        if entry.first_date and entry.first_date != today:
            del pending[key]


def _needs_late_final_confirm(sig: TradeSignal) -> bool:
    if sig.action != "卖出":
        return False
    if sig.signal_kind in ("止损", "时间止损", "日内走弱"):
        return False
    return sig.signal_kind in sell_kinds_requiring_late_final()


def _waiting_late_session(
    entry: PendingSignal,
    sig: TradeSignal,
    now: datetime,
    conf: dict,
) -> bool:
    """持续条件已满足，但趋势类卖须等 14:30 后成交。"""
    if not _needs_late_final_confirm(sig):
        return False
    if is_late_session_for_trend_sell(now):
        return False
    return persistence_satisfied(
        entry,
        now,
        persistence_minutes=conf["persistence_minutes"],
        min_consecutive_runs=conf["min_consecutive_runs"],
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
    if conf.get("same_day_window") and entry.action == "买入":
        if entry.first_date and entry.first_date != today:
            return True
        return False
    return elapsed > conf["max_window_minutes"]


def _try_execute(
    sig: TradeSignal,
    entry: PendingSignal,
    conf: dict,
    now: datetime,
    ctx: ScoreContext | None = None,
) -> tuple[TradeSignal | None, str]:
    """持续确认达标后生成可执行信号；返回 (exec_sig, audit_status)。"""
    persist = conf["persistence_minutes"]
    use_tm = conf.get("use_trading_minutes", False)
    elapsed = _elapsed_minutes(entry, now, use_trading_minutes=use_tm)

    if _waiting_late_session(entry, sig, now, conf):
        return None, "持续条件已满足，等待14:30后执行"

    if not persistence_satisfied(
        entry,
        now,
        persistence_minutes=persist,
        min_consecutive_runs=conf["min_consecutive_runs"],
        use_trading_minutes=use_tm,
    ):
        return (
            None,
            f"持续确认中（{elapsed:.0f}/{persist:.0f}分，{entry.count}/{conf['min_consecutive_runs']}轮）",
        )

    if sig.action == "买入" and conf.get("verify_before_execute") and ctx is not None:
        from quant.signals.buy import verify_buy_signal_still_valid

        if not verify_buy_signal_still_valid(sig.code, ctx, mode=ctx.mode or "during_market"):
            return None, "持续确认完成但买点已失效，暂不成交"

    kind = sig.signal_kind or "默认"
    exec_sig = TradeSignal(
        action=sig.action,
        code=sig.code,
        name=sig.name,
        price=sig.price,
        quantity=sig.quantity,
        strategy=sig.strategy,
        reason=(
            f"持续确认完成（{elapsed:.0f}分/{persist:.0f}分，"
            f"{entry.count}轮）；{sig.reason}"
        ),
        sell_type=sig.sell_type,
        signal_kind=kind,
        confirmation_stage=entry.count,
    )
    status = "持续确认完成，可交易"
    if _needs_late_final_confirm(sig):
        status = "持续确认完成（14:30后），可交易"
    return exec_sig, status


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
    _purge_cross_day_buy_pending(pending, today)
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
        waiting_late = _waiting_late_session(entry, sig, now, conf)

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
        max_miss = int(conf.get("max_miss_streak", 0))
        if max_miss > 0 and entry.miss_streak < max_miss:
            entry.miss_streak += 1
            pending[key] = entry
        else:
            del pending[key]

    save_pending(pending)
    return executable, audit


def _audit_row(sig: TradeSignal, entry: PendingSignal, status: str, *, executable: bool) -> dict:
    return {
        "股票代码": sig.code,
        "股票名称": sig.name,
        "方向": sig.action,
        "信号类型": entry.signal_kind,
        "确认次数": entry.count,
        "状态": status,
        "可执行": executable,
        "理由": sig.reason,
    }
