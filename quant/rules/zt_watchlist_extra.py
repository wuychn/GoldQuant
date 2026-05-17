"""涨停板战法加自选附加规则（市场炸板率、一字剔除）——独立文件以降低 zt_watchlist.py 体量。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


class ZTWatchlistNotOneBoardRule(Rule):
    """复盘加自选：今日涨停池中排除一字竞价封死（盘口今开≈涨停价）。"""

    def default_params(self) -> dict:
        return {"open_limit_epsilon": 0.002}

    @property
    def name(self) -> str:
        return "ZT加自选剔除一字"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})

        today_open = pankou.get("今开")
        limit_up = pankou.get("涨停")

        if today_open is None or limit_up is None:
            return self._skip(f"{name}盘口缺少今开/涨停，无法用一字剔除")

        try:
            open_f = float(today_open)
            limit_f = float(limit_up)
        except (TypeError, ValueError):
            return self._skip(f"{name}盘口今开或涨停字段格式异常")

        if abs(open_f - limit_f) < float(self.params.get("open_limit_epsilon", 0.002)):
            return self._fail(
                f"一字涨停封板特征（今开{open_f:.2f}=涨停参考{limit_f:.2f}），按配置不参与涨停板战法自选"
            )
        return self._pass(f"非一字涨停开盘（今开{open_f:.2f}≠涨停参考{limit_f:.2f}）")


class ZTMarketBlastRateGateRule(Rule):
    """市场整体炸板率门禁（赚钱效应：(涨停−真实涨停)/涨停）。"""

    def default_params(self) -> dict:
        return {"max_market_blast_rate_pct": 52.0}

    @property
    def name(self) -> str:
        return "ZT市场炸板率"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        mx = float(self.params.get("max_market_blast_rate_pct", 52.0))
        br = ctx.get_market_blast_rate_pct()
        if br is None:
            return self._skip("赚钱效应缺少『涨停』/『真实涨停』，无法估算市场炸板率")

        line = (
            "炸板率≈触及涨停但未以涨停口径收盘占比："
            f"(涨停计数−真实涨停)/涨停计数={br:.2f}%"
        )
        if br <= mx:
            return self._pass(f"{line}；≤阈值{mx}%")

        return self._fail(f"{line}；超阈值{mx}%，市场情绪下打板风险偏高")
