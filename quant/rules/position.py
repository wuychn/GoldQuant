"""仓位联动 + 每日亏损限额规则。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


# 仓位限制参数
_POSITION_LIMITS = {
    "强势": {
        "总仓位": 0.80,
        "涨停单票": 0.5,
        "龙回头单票": 0.5,
        "主升浪单票": 0.5,
        "最多持仓": 5,
    },
    "震荡": {
        "总仓位": 0.50,
        "涨停单票": 0.3,
        "龙回头单票": 0.3,
        "主升浪单票": 0.3,
        "最多持仓": 3,
    },
    "弱势": {
        "总仓位": 0.20,
        "涨停单票": 0.1,
        "龙回头单票": 0.1,
        "主升浪单票": 0.1,
        "最多持仓": 2,
    },
}


class PositionLimitRule(Rule):
    """仓位联动：根据市场状态验证是否可新开仓。

    依赖 ctx.extra["market_state_verdict"]（需先运行 MarketStateDeterminationRule）。
    """

    def default_params(self):
        return {
            "max_positions_strong": 5,
            "max_positions_neutral": 3,
            "max_positions_weak": 2,
        }

    @property
    def name(self) -> str:
        return "仓位联动"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        verdict = ctx.extra.get("market_state_verdict", "震荡")
        limits = _POSITION_LIMITS.get(verdict, _POSITION_LIMITS["震荡"])

        max_total = limits["总仓位"]
        # 从 params 获取持仓数上限（可通过YAML覆盖）
        if verdict == "强势":
            max_count = int(self.params["max_positions_strong"])
        elif verdict == "弱势":
            max_count = int(self.params["max_positions_weak"])
        else:
            max_count = int(self.params["max_positions_neutral"])

        # 持仓数量检查
        current_count = len(ctx.holdings)
        if current_count >= max_count:
            return self._fail(
                f"市场{verdict}，最多持{max_count}只，当前已持{current_count}只，不可新开仓"
            )

        # 仓位比例检查
        current_ratio = ctx.current_position_ratio()
        if current_ratio >= max_total:
            return self._fail(
                f"市场{verdict}，总仓位上限{max_total*100:.0f}%，当前{current_ratio*100:.1f}%，不可新开仓"
            )

        # 单票仓位提示
        stock = ctx.target_stock
        strategy = str(stock.get("战法", "")).strip()
        if "涨停" in strategy:
            max_single = limits["涨停单票"]
        elif "主升浪" in strategy:
            max_single = limits["主升浪单票"]
        elif "龙回头" in strategy:
            max_single = limits["龙回头单票"]
        else:
            max_single = limits["龙回头单票"]

        ctx.extra["max_single_position"] = max_single
        ctx.extra["max_total_position"] = max_total

        return self._pass(
            f"市场{verdict}：总仓位≤{max_total*100:.0f}%（当前{current_ratio*100:.1f}%），"
            f"单票≤{max_single*100:.0f}%，最多{max_count}只（当前{current_count}只）",
            verdict=verdict,
            max_single=max_single,
            max_total=max_total,
        )


class DailyLossLimitRule(Rule):
    """每日亏损限额：`ctx.daily_pnl` 为当日已实现盈亏（成交汇总）占当前总权益比例达阈值则触发。"""

    def default_params(self):
        return {"max_daily_loss_pct": -3.0}

    @property
    def name(self) -> str:
        return "每日亏损限额"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        if ctx.fund <= 0:
            return self._skip("总资金数据缺失")

        threshold = float(self.params["max_daily_loss_pct"]) / 100.0
        loss_ratio = ctx.daily_pnl / ctx.fund

        if loss_ratio <= threshold:
            return self._fail(
                f"当日亏损{loss_ratio*100:.2f}%已达限额{threshold*100:.0f}%，次日全天空仓"
            )

        return self._pass(
            f"当日盈亏{loss_ratio*100:.2f}%，未触及限额{threshold*100:.0f}%"
        )
