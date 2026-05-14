"""市场状态机判定规则 + 情绪退潮预警。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


def _strong_hard_gates(
    ctx: RuleContext,
    *,
    index_min: float,
    zt_min: int,
    dt_max: int,
) -> tuple[bool, list[str]]:
    """判「强势」的硬条件（与 6 项软评分独立）：全部满足才允许维持强势。

    数据优先来自 payload「赚钱效应」（上涨/下跌/涨停/跌停），与 data/during_market 样例一致；
    上证涨跌幅来自大盘指数或市场状态机。
    """
    failed: list[str] = []

    ch = ctx.get_shangzheng_change()
    if ch is None:
        failed.append("上证涨跌幅缺失")
    elif ch < index_min:
        failed.append(f"上证涨跌幅{ch:.2f}%＜门槛{index_min}%")

    up = ctx.get_profit_effect_advancers()
    down = ctx.get_profit_effect_decliners()
    if up is None or down is None:
        failed.append("赚钱效应上涨/下跌家数缺失")
    elif up <= down:
        failed.append(f"涨跌家数未占优(涨{up}/跌{down})")

    zt = ctx.get_profit_effect_limit_up()
    if zt is None:
        failed.append("赚钱效应涨停家数缺失")
    elif zt < zt_min:
        failed.append(f"涨停{zt}只＜门槛{zt_min}只")

    dt = ctx.get_profit_effect_limit_down()
    if dt is None:
        failed.append("赚钱效应跌停家数缺失")
    elif dt > dt_max:
        failed.append(f"跌停{dt}只＞上限{dt_max}只")

    return (not failed, failed)


class MarketStateDeterminationRule(Rule):
    """根据 6 项软指标评分判定市场状态（强势/震荡/弱势），由规则引擎执行。

    「强势」在软评分通过后还须通过硬门槛：上证涨跌幅、赚钱效应涨跌家数占优、
    涨停家数、跌停家数（见 default_params 中 strong_gate_*）。任一不满足则降为「震荡」。

    判定结果写入 ctx.extra["market_state_verdict"]。
    """

    def default_params(self):
        return {
            "weak_threshold": 2,
            "strong_threshold": 4,
            # --- 判「强势」硬条件（不达标则从强势降为震荡，不改变弱势判定）---
            "strong_gate_index_min": 0.0,  # 上证涨跌幅 ≥ 此值(%)
            "strong_gate_zt_min": 50,  # 涨停家数 ≥ 赚钱效应「涨停」
            "strong_gate_dt_max": 40,  # 跌停家数 ≤（赚钱效应「跌停」）
        }

    @property
    def name(self) -> str:
        return "市场状态机判定"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        scores = {"强势": 0, "震荡": 0, "弱势": 0}
        weak_threshold = int(self.params["weak_threshold"])
        strong_threshold = int(self.params["strong_threshold"])

        # 1. 上证相对20日均线
        sz_vs_ma20 = ctx.get_sz_vs_ma20()
        if sz_vs_ma20 is not None:
            if sz_vs_ma20 > 1:
                scores["强势"] += 1
            elif sz_vs_ma20 < -1:
                scores["弱势"] += 1
            else:
                scores["震荡"] += 1

        # 2. 昨日涨停表现涨跌幅均值
        zt_avg = ctx.get_yesterday_zt_avg_change()
        if zt_avg is not None:
            if zt_avg > 2:
                scores["强势"] += 1
            elif zt_avg < 0:
                scores["弱势"] += 1
            else:
                scores["震荡"] += 1

        # 3. 两市量比
        vol_ratio = ctx.get_volume_ratio()
        if vol_ratio is not None:
            if vol_ratio > 1.1:
                scores["强势"] += 1
            elif vol_ratio < 0.9:
                scores["弱势"] += 1
            else:
                scores["震荡"] += 1

        # 4. 连板高度
        max_boards = ctx.get_max_consecutive_boards()
        if max_boards is not None:
            if max_boards >= 5:
                scores["强势"] += 1
            elif max_boards <= 2:
                scores["弱势"] += 1
            else:
                scores["震荡"] += 1

        # 5. 涨停家数
        zt_count = ctx.get_limit_up_count()
        if zt_count is not None:
            if zt_count >= 60:
                scores["强势"] += 1
            elif zt_count < 30:
                scores["弱势"] += 1
            else:
                scores["震荡"] += 1

        # 6. 今日大盘实时涨跌
        sz_change = ctx.get_shangzheng_change()
        if sz_change is not None:
            if sz_change > 0.5:
                scores["强势"] += 1
            elif sz_change < -0.5:
                scores["弱势"] += 1
            else:
                scores["震荡"] += 1

        # 判定：使用可配置阈值（软评分）
        verdict = "震荡"
        if scores["强势"] >= strong_threshold:
            verdict = "强势"
        elif scores["弱势"] >= weak_threshold + 1:
            verdict = "弱势"
        elif scores["强势"] >= weak_threshold + 1:
            verdict = "强势"

        gate_failed: list[str] = []
        soft_was_strong = verdict == "强势"
        if soft_was_strong:
            gate_ok, gate_failed = _strong_hard_gates(
                ctx,
                index_min=float(self.params["strong_gate_index_min"]),
                zt_min=int(self.params["strong_gate_zt_min"]),
                dt_max=int(self.params["strong_gate_dt_max"]),
            )
            if not gate_ok:
                verdict = "震荡"
            ctx.extra["market_state_strong_gates_ok"] = gate_ok
            ctx.extra["market_state_strong_gate_failures"] = (
                gate_failed if not gate_ok else []
            )
        else:
            ctx.extra["market_state_strong_gates_ok"] = None
            ctx.extra["market_state_strong_gate_failures"] = []

        ctx.extra["market_state_verdict"] = verdict
        ctx.extra["market_state_scores"] = scores

        detail = (
            f"强势{scores['强势']}项/震荡{scores['震荡']}项/弱势{scores['弱势']}项 → {verdict}"
        )
        if verdict == "震荡" and soft_was_strong and gate_failed:
            detail += f"（强势硬条件未通过：{'；'.join(gate_failed)}）"
        return self._pass(detail, verdict=verdict, scores=scores)


class SentimentDecayWarningRule(Rule):
    """情绪退潮预警：昨日涨停表现均值<0% 且最高连板数较前日下降。

    退潮时涨停板战法当日不开新仓。
    结果写入 ctx.extra["sentiment_decay"] = True/False。
    """

    def default_params(self):
        return {"volume_decay_days": 3, "limit_down_surge": 2.0}

    @property
    def name(self) -> str:
        return "情绪退潮预警"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        zt_avg = ctx.get_yesterday_zt_avg_change()
        if zt_avg is None:
            ctx.extra["sentiment_decay"] = False
            return self._skip("昨日涨停表现数据缺失，无法判定")

        # 情绪退潮核心指标：昨日涨停表现涨跌幅均值<0%
        if zt_avg >= 0:
            ctx.extra["sentiment_decay"] = False
            return self._pass(f"昨日涨停表现均值={zt_avg:.2f}%≥0%，未退潮")

        # 退潮确认
        ctx.extra["sentiment_decay"] = True
        return self._fail(
            f"昨日涨停表现均值={zt_avg:.2f}%<0%，情绪退潮，涨停板战法不开新仓"
        )
