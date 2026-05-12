"""共用约束规则：标的池过滤、极端行情熔断、止损冷却期。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


class StockPoolFilterRule(Rule):
    """标的池过滤：沪深A股主板60/00、创业板30。排除ST、科创板688、北交所8、新股<60日。"""

    def default_params(self):
        return {
            "exclude_st": True,
            "exclude_kcb": True,
            "exclude_bse": True,
            "min_listing_days": 60,
        }

    @property
    def name(self) -> str:
        return "标的池过滤"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()

        if not code:
            return self._fail("股票代码为空")

        # ST 检查
        if self.params["exclude_st"] and ("ST" in name.upper() or "*ST" in name.upper()):
            return self._fail(f"{name}({code})为ST股，排除")

        # 板块检查
        if self.params["exclude_kcb"] and code.startswith("688"):
            return self._fail(f"{name}({code})为科创板，排除")
        if self.params["exclude_bse"] and (code.startswith("8") or code.startswith("4")):
            return self._fail(f"{name}({code})为北交所，排除")
        if not (code.startswith("60") or code.startswith("00") or code.startswith("30")):
            return self._fail(f"{name}({code})不在允许板块(60/00/30)，排除")

        # 新股检查
        min_days = int(self.params["min_listing_days"])
        listing_days = stock.get("上市天数") or stock.get("listing_days")
        if listing_days is not None:
            try:
                if int(listing_days) < min_days:
                    return self._fail(f"{name}({code})上市不足{min_days}日，排除")
            except (TypeError, ValueError):
                pass

        return self._pass(f"{name}({code})符合标的池要求")


class ExtremeMarketCircuitBreakerRule(Rule):
    """极端行情熔断：大盘跌幅超阈值时不开新仓。"""

    def default_params(self):
        return {"index_drop_pct": -3.0}

    @property
    def name(self) -> str:
        return "极端行情熔断"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        threshold = float(self.params["index_drop_pct"])
        reasons = []

        # 条件1：大盘跌幅超阈值
        sz_change = ctx.get_shangzheng_change()
        if sz_change is not None and sz_change < threshold:
            reasons.append(f"上证跌幅{sz_change:.2f}%<{threshold}%")

        # 条件2：连续缩量
        if len(ctx.capital_flow) >= 3:
            amounts = []
            for item in ctx.capital_flow[:3]:
                amt = item.get("成交额")
                if amt is not None:
                    try:
                        amounts.append(float(amt))
                    except (TypeError, ValueError):
                        pass
            if len(amounts) == 3:
                is_decreasing = amounts[0] < amounts[1] < amounts[2]
                is_below_80 = amounts[0] < amounts[2] * 0.8
                if is_decreasing and is_below_80:
                    reasons.append(
                        f"连续缩量（近3日成交额{amounts[0]:.0f}<{amounts[1]:.0f}<{amounts[2]:.0f}，"
                        f"且最新<前第3日的80%={amounts[2]*0.8:.0f}）"
                    )

        if reasons:
            return self._fail("；".join(reasons) + " → 全天不开新仓")

        return self._pass("大盘无极端行情")


class StopLossCoolingRule(Rule):
    """止损冷却期：某标的触发止损卖出后N个交易日内不得再次买入。"""

    def default_params(self):
        return {"cooling_days": 5}

    @property
    def name(self) -> str:
        return "止损冷却期"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        cooling_days = int(self.params["cooling_days"])

        if not code:
            return self._skip("目标股票代码为空")

        if ctx.is_in_stoploss_cooling(code, cooling_days):
            return self._fail(
                f"{name}({code})处于止损冷却期（{cooling_days}个交易日内），不可买入"
            )

        return self._pass(f"{name}({code})不在止损冷却期内")
