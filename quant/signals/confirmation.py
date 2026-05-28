"""三确认交易机制：同一标的同一方向需连续 N 次信号，且间隔在 [min, max] 分钟内。

第 1 次：记录信号
第 2 次：确认
第 3 次：再确认 → 允许 execute_signals

趋势类卖出（破5日线/趋势衰竭/评分走弱）特殊规则 — 神奇2点30：
- 第 1、2 次可在 14:30 前产生并累计
- 第 3 次（最终确认）须在 14:30 之后（通常 ~14:40）才完成并允许成交
- 已二次确认后等待最终信号时，当日不因 max_interval 超时重置

间隔规则（按市场状态 强势/震荡/弱势 可配，ML 可校准）：
- 距上次信号 < min_interval → 忽略（防抖动）
- 距上次信号 > max_interval → 重置为第 1 次
- 在 [min, max] 内 → 计数 +1

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
    count: int
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
    cfg = load_gates_config().get("confirmation") or {}
    regime = infer_regime(ctx.payload)
    block = cfg.get(regime) or cfg.get("震荡") or {}
    kind_block = (cfg.get("by_kind") or {}).get(signal_kind) or {}
    return {
        "required_count": int(cfg.get("required_count", 3)),
        "min_interval_minutes": float(
            kind_block.get("min_interval_minutes", block.get("min_interval_minutes", 20))
        ),
        "max_interval_minutes": float(
            kind_block.get("max_interval_minutes", block.get("max_interval_minutes", 180))
        ),
        "regime": regime,
    }


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
    if sig.signal_kind in ("止损", "时间止损"):
        return False
    return sig.signal_kind in sell_kinds_requiring_late_final()


def _holding_late_final(entry: PendingSignal, required: int, sig: TradeSignal, last_dt: datetime, now: datetime) -> bool:
    """当日已达二次确认、等待 14:30 后最终确认。"""
    if not _needs_late_final_confirm(sig):
        return False
    if entry.count < required - 1:
        return False
    return last_dt.date() == now.date()


def apply_three_confirmations(
    raw_signals: list[TradeSignal],
    ctx: ScoreContext,
) -> tuple[list[TradeSignal], list[dict]]:
    """返回可执行信号 + 审计日志（含各阶段确认状态）。"""
    now = _now()
    pending = load_pending()
    executable: list[TradeSignal] = []
    audit: list[dict] = []

    for sig in raw_signals:
        kind = sig.signal_kind or "默认"
        key = f"{sig.code}|{sig.action}|{kind}"
        conf = confirmation_config(ctx, kind)
        required = conf["required_count"]
        min_iv = conf["min_interval_minutes"]
        max_iv = conf["max_interval_minutes"]
        regime = conf["regime"]
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
            audit.append(_audit_row(sig, entry, "首次信号已记录", executable=False))
            continue

        last_dt = _parse_ts(entry.last_at)
        if last_dt is None:
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
            audit.append(_audit_row(sig, entry, "时间戳异常，重置为首次", executable=False))
            continue

        delta_min = (now - last_dt).total_seconds() / 60.0
        late_hold = _holding_late_final(entry, required, sig, last_dt, now)

        if delta_min < min_iv:
            audit.append(
                _audit_row(
                    sig,
                    entry,
                    f"间隔{delta_min:.0f}分<{min_iv:.0f}分，忽略",
                    executable=False,
                )
            )
            continue

        if delta_min > max_iv:
            if late_hold and not is_late_session_for_trend_sell(now):
                audit.append(
                    _audit_row(
                        sig,
                        entry,
                        "已二次确认，等待14:30后最终确认（间隔超时当日不重置）",
                        executable=False,
                    )
                )
                continue
            if not (late_hold and is_late_session_for_trend_sell(now)):
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
                audit.append(_audit_row(sig, entry, f"间隔过久重置为首次", executable=False))
                continue

        next_count = entry.count + 1
        if _needs_late_final_confirm(sig) and next_count >= required and not is_late_session_for_trend_sell(now):
            audit.append(
                _audit_row(
                    sig,
                    entry,
                    f"已{entry.count}次确认，等待14:30后最终确认",
                    executable=False,
                )
            )
            continue

        entry.count = next_count
        entry.last_at = now.isoformat()
        entry.last_reason = sig.reason
        entry.regime = regime
        pending[key] = entry

        if entry.count >= required:
            exec_sig = TradeSignal(
                action=sig.action,
                code=sig.code,
                name=sig.name,
                price=sig.price,
                quantity=sig.quantity,
                strategy=sig.strategy,
                reason=f"三确认完成({entry.count}/{required})；{sig.reason}",
                sell_type=sig.sell_type,
                signal_kind=kind,
                confirmation_stage=required,
            )
            executable.append(exec_sig)
            del pending[key]
            audit.append(_audit_row(sig, entry, "三确认完成（神奇2点30最终确认），可交易", executable=True))
        elif entry.count == 2:
            audit.append(_audit_row(sig, entry, "第二次确认", executable=False))
        else:
            audit.append(_audit_row(sig, entry, f"第{entry.count}次信号", executable=False))

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
