"""每日决策输出：目标组合 + 偏离当前持仓 + 出场信号。

辅助决策的核心产物：一张「今日该做什么」的卡，而非自动下单。
人根据卡片执行，并把实际操作回填到偏离日志。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Action:
    code: str
    side: str  # 'buy' / 'sell' / 'hold' / 'reduce' / 'add'
    target_weight: float
    current_weight: float
    delta_weight: float
    reason: str = ""


@dataclass
class DecisionCard:
    date: str
    target_weights: dict[str, float]
    actions: list[Action] = field(default_factory=list)
    exit_signals: list[dict] = field(default_factory=list)  # [{code, reason, price}]
    alpha_top: list[tuple[str, float]] = field(default_factory=list)  # [(code, alpha)]


def build_decision_card(
    date: str,
    alpha: dict[str, float],
    target_weights: dict[str, float],
    current_weights: dict[str, float],
    exit_signals: list[dict] | None = None,
    *,
    trade_threshold: float = 0.01,
) -> DecisionCard:
    """根据目标权重与当前权重生成动作清单。"""
    actions: list[Action] = []
    codes = set(target_weights) | set(current_weights)
    exit_codes = {s.get("code") for s in (exit_signals or [])}
    for code in codes:
        tw = target_weights.get(code, 0.0)
        cw = current_weights.get(code, 0.0)
        delta = tw - cw
        if code in exit_codes and cw > 0:
            actions.append(Action(code, "sell", tw, cw, delta, reason="exit_signal"))
            continue
        if tw <= 0 and cw > trade_threshold:
            actions.append(Action(code, "sell", tw, cw, delta, reason="out_of_target"))
        elif tw > 0 and cw <= 0:
            actions.append(Action(code, "buy", tw, cw, delta, reason="new_position"))
        elif abs(delta) > trade_threshold:
            side = "add" if delta > 0 else "reduce"
            actions.append(Action(code, side, tw, cw, delta, reason="rebalance"))
        else:
            actions.append(Action(code, "hold", tw, cw, delta, reason="within_buffer"))

    actions.sort(key=lambda a: -abs(a.delta_weight))
    top = sorted(alpha.items(), key=lambda kv: -kv[1])[:10]
    return DecisionCard(
        date=date,
        target_weights=dict(target_weights),
        actions=actions,
        exit_signals=list(exit_signals or []),
        alpha_top=top,
    )


def card_to_text(card: DecisionCard) -> str:
    """渲染为可读文本（终端/日志）。"""
    lines = [f"=== 决策卡 {card.date} ===", "目标组合:"]
    for c, w in sorted(card.target_weights.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {c}: {w*100:.1f}%")
    lines.append("动作:")
    for a in card.actions:
        if a.side == "hold":
            continue
        lines.append(f"  [{a.side:5s}] {a.code} 目标{a.target_weight*100:.1f}% 当前{a.current_weight*100:.1f}% Δ{a.delta_weight*100:+.1f}% ({a.reason})")
    if card.exit_signals:
        lines.append("出场信号:")
        for s in card.exit_signals:
            lines.append(f"  {s.get('code')} {s.get('reason')} @ {s.get('price')}")
    lines.append("Alpha Top10:")
    for c, v in card.alpha_top:
        lines.append(f"  {c}: {v:.3f}")
    return "\n".join(lines)
