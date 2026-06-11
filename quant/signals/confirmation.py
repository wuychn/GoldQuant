"""信号持续确认：条件自首次满足起须连续保持足够时长与调度轮次，方可成交。

与旧「计数三确认」不同，中间任一轮条件消失会清零 pending（见本轮未见信号则删除条目）。
趋势类卖出（破5日线/趋势衰竭/评分走弱）在持续条件已满足后，仍须 14:30 后执行。

持久化：~/.quant/state/signal_pending.json
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from quant.config import load_gates_config
from quant.scoring.context import ScoreContext, infer_regime
from quant.signals.models import TradeSignal
from quant.store.paths import state_file
from quant.trading_hours import is_late_session_for_trend_sell, sell_kinds_requiring_late_final

_SH_TZ = ZoneInfo("Asia/Shanghai")
_PENDING_FILE = "signal_pending.json"


@dataclass
class PendingSignal:
    code: str
    action: str
    signal_kind: str
    count: int  # 连续满足条件的调度轮次
    first_at: str
    last_at: str
    regime: str
    last_reason: str = ""

    def key(self) -> str:
        return f"{self.code}|{self.action}|{self.signal_kind}"


def _now() -> datetime:
    return datetime.now(_SH_TZ)


def _parse_ts(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_SH_TZ)
        return dt.astimezone(_SH_TZ)
    except ValueError:
        return None


def confirmation_config(ctx: ScoreContext, signal_kind: str = "") -> dict:
    """按 signal_kind → 市场状态 → 全局默认 解析持续确认参数。"""
    cfg = load_gates_config().get("confirmation") or {}
    regime = infer_regime(ctx.payload)
    regime_block = cfg.get(regime) or cfg.get("震荡") or {}
    kind_block = (cfg.get("by_kind") or {}).get(signal_kind) or {}

    persist = kind_block.get("persistence_minutes")
    if persist is None:
        persist = regime_block.get("persistence_minutes")
    if persist is None:
        persist = cfg.get("default_persistence_minutes", 15)

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

    return {
        "persistence_minutes": persist_f,
        "min_consecutive_runs": min_runs_i,
        "max_window_minutes": float(max_window),
        "regime": regime,
    }


def persistence_satisfied(
    entry: PendingSignal,
    now: datetime,
    *,
    persistence_minutes: float,
    min_consecutive_runs: int,
) -> bool:
    """是否达到持续时长 + 连续轮次。"""
    first_dt = _parse_ts(entry.first_at)
    if first_dt is None:
        return False
    if persistence_minutes <= 0:
        return entry.count >= 1
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
            try:
                out[k] = PendingSignal(**v)
            except TypeError:
                continue
    return out


def save_pending(pending: dict[str, PendingSignal]) -> None:
    path = state_file(_PENDING_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: asdict(v) for k, v in pending.items()}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _needs_late_final_confirm(sig: TradeSignal) -> bool:
    if sig.action != "卖出":
        return False
    if sig.signal_kind in ("止损", "时间止损", "日内走弱"):
        return False
    return sig.signal_kind in sell_kinds_requiring_late_final()


def _waiting_late_session(entry: PendingSignal, sig: TradeSignal, now: datetime, conf: dict) -> bool:
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


def _elapsed_minutes(entry: PendingSignal, now: datetime) -> float:
    first_dt = _parse_ts(entry.first_at)
    if first_dt is None:
        return 0.0
    return (now - first_dt).total_seconds() / 60.0


def _try_execute(
    sig: TradeSignal,
    entry: PendingSignal,
    conf: dict,
    now: datetime,
) -> tuple[TradeSignal | None, str]:
    """持续确认达标后生成可执行信号；返回 (exec_sig, audit_status)。"""
    persist = conf["persistence_minutes"]
    elapsed = _elapsed_minutes(entry, now)

    if _waiting_late_session(entry, sig, now, conf):
        return None, "持续条件已满足，等待14:30后执行"

    if not persistence_satisfied(
        entry,
        now,
        persistence_minutes=persist,
        min_consecutive_runs=conf["min_consecutive_runs"],
    ):
        return (
            None,
            f"持续确认中（{elapsed:.0f}/{persist:.0f}分，{entry.count}/{conf['min_consecutive_runs']}轮）",
        )

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
    """持续确认：返回可执行信号 + 审计日志。

    scope_action：仅清理该方向下本轮未出现的 pending（买卖分两次调用时必传，避免误删对向）。
    """
    _clear_opposite_pending(raw_signals)
    now = _now()
    pending = load_pending()
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
        conf = confirmation_config(ctx, kind)
        regime = conf["regime"]
        max_window = conf["max_window_minutes"]
        entry = pending.get(key)

        if entry is None:
            entry = PendingSignal(
                code=sig.code,
                action=sig.action,
                signal_kind=kind,
                count=1,
                first_at=now.isoformat(),
                last_at=now.isoformat(),
                regime=regime,
                last_reason=sig.reason,
            )
            pending[key] = entry
            exec_sig, status = _try_execute(sig, entry, conf, now)
            if exec_sig:
                executable.append(exec_sig)
                del pending[key]
                audit.append(_audit_row(sig, entry, status, executable=True))
            else:
                audit.append(_audit_row(sig, entry, status, executable=False))
            continue

        first_dt = _parse_ts(entry.first_at)
        if first_dt is None:
            entry = PendingSignal(
                code=sig.code,
                action=sig.action,
                signal_kind=kind,
                count=1,
                first_at=now.isoformat(),
                last_at=now.isoformat(),
                regime=regime,
                last_reason=sig.reason,
            )
            pending[key] = entry
            audit.append(_audit_row(sig, entry, "时间戳异常，重新计时", executable=False))
            continue

        elapsed = _elapsed_minutes(entry, now)
        waiting_late = _waiting_late_session(entry, sig, now, conf)

        if elapsed > max_window and not waiting_late:
            entry = PendingSignal(
                code=sig.code,
                action=sig.action,
                signal_kind=kind,
                count=1,
                first_at=now.isoformat(),
                last_at=now.isoformat(),
                regime=regime,
                last_reason=sig.reason,
            )
            pending[key] = entry
            audit.append(_audit_row(sig, entry, "持续超时，重新计时", executable=False))
            continue

        entry.count += 1
        entry.last_at = now.isoformat()
        entry.last_reason = sig.reason
        entry.regime = regime
        pending[key] = entry

        exec_sig, status = _try_execute(sig, entry, conf, now)
        if exec_sig:
            executable.append(exec_sig)
            del pending[key]
            audit.append(_audit_row(sig, entry, status, executable=True))
        else:
            audit.append(_audit_row(sig, entry, status, executable=False))

    for key in list(pending.keys()):
        entry = pending[key]
        if entry.action in scope_actions and key not in seen_keys:
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
