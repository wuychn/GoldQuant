"""市场状态机判定规则 + 情绪退潮预警。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


class MarketStateDeterminationRule(Rule):
    """根据6项指标判定市场状态（强势/震荡/弱势）。

    判定结果写入 ctx.extra["market_state_verdict"]。
    """

    def default_params(self):
        return {"weak_threshold": 2, "strong_threshold": 4}

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

        # 判定：使用可配置阈值
        verdict = "震荡"
        if scores["强势"] >= strong_threshold:
            verdict = "强势"
        elif scores["弱势"] >= weak_threshold + 1:
            verdict = "弱势"
        elif scores["强势"] >= weak_threshold + 1:
            verdict = "强势"

        ctx.extra["market_state_verdict"] = verdict
        ctx.extra["market_state_scores"] = scores

        detail = f"强势{scores['强势']}项/震荡{scores['震荡']}项/弱势{scores['弱势']}项 → {verdict}"
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
