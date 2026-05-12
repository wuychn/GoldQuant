"""龙回头战法-持股监控 + 卖出规则。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


# ---------------------------------------------------------------------------
# 持股监控
# ---------------------------------------------------------------------------

class LHTAboveMA5Rule(Rule):
    """持股监控：站稳5日线（最新≥均线5日）。"""

    @property
    def name(self) -> str:
        return "LHT站稳5日线"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})
        tech = stock.get("技术指标", {})

        latest = pankou.get("最新")
        ma5 = tech.get("均线5日")

        if latest is None or ma5 is None:
            return self._skip(f"{name}缺失最新价或均线5日")

        try:
            latest_f = float(latest)
            ma5_f = float(ma5)
        except (TypeError, ValueError):
            return self._skip(f"{name}数据格式异常")

        if latest_f >= ma5_f:
            return self._pass(f"最新{latest_f:.2f}≥MA5({ma5_f:.2f})，正常持有")
        return self._fail(f"最新{latest_f:.2f}<MA5({ma5_f:.2f})，偏弱")


class LHTMABreakdownRule(Rule):
    """弱势出局信号1：均线破位（最新<均线5日×0.98）。"""

    def __init__(self, *, breakdown_ratio: float = 0.98, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.breakdown_ratio = breakdown_ratio

    @property
    def name(self) -> str:
        return "LHT均线破位"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})
        tech = stock.get("技术指标", {})

        latest = pankou.get("最新")
        ma5 = tech.get("均线5日")

        if latest is None or ma5 is None:
            return self._skip(f"{name}缺失数据")

        try:
            latest_f = float(latest)
            ma5_f = float(ma5)
        except (TypeError, ValueError):
            return self._skip(f"{name}数据格式异常")

        threshold = ma5_f * self.breakdown_ratio
        if latest_f < threshold:
            return self._fail(
                f"最新{latest_f:.2f}<MA5×{self.breakdown_ratio}={threshold:.2f}，均线破位触发减仓"
            )
        return self._pass(f"最新{latest_f:.2f}≥MA5×{self.breakdown_ratio}={threshold:.2f}，未破位")


class LHTCapitalOutflowRule(Rule):
    """弱势出局信号2：资金流出（主力净流入<0）。"""

    @property
    def name(self) -> str:
        return "LHT资金流出"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()

        capital_flow = stock.get("个股资金流", [])
        if not capital_flow or not isinstance(capital_flow, list):
            return self._skip(f"{name}无资金流数据")

        last_flow = capital_flow[-1] if isinstance(capital_flow[-1], dict) else {}
        net_amt = last_flow.get("主力净流入-净额", last_flow.get("净额"))

        if net_amt is None:
            return self._skip(f"{name}资金流无净额数据")

        try:
            net_f = float(net_amt)
        except (TypeError, ValueError):
            return self._skip(f"{name}净额数据格式异常")

        if net_f < 0:
            return self._fail(f"主力净流出{net_f:.0f}，触发弱势减仓信号")
        return self._pass(f"主力净流入{net_f:.0f}≥0，资金面正常")


class LHTReboundPullbackRule(Rule):
    """弱势出局信号3：反弹回撤（从最高回落≥3%）。"""

    def __init__(self, *, pullback_pct: float = 3.0, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.pullback_pct = pullback_pct

    @property
    def name(self) -> str:
        return "LHT反弹回撤"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})

        latest = pankou.get("最新")
        highest = pankou.get("最高")

        if latest is None or highest is None:
            return self._skip(f"{name}缺失最新/最高价")

        try:
            latest_f = float(latest)
            high_f = float(highest)
        except (TypeError, ValueError):
            return self._skip(f"{name}数据格式异常")

        if high_f == 0:
            return self._skip("最高价为0")

        pullback = (high_f - latest_f) / high_f * 100

        if pullback >= self.pullback_pct:
            return self._fail(
                f"从最高{high_f:.2f}回落{pullback:.2f}%≥{self.pullback_pct}%，触发减仓"
            )
        return self._pass(f"从最高回落{pullback:.2f}%<{self.pullback_pct}%，正常持有")


# ---------------------------------------------------------------------------
# 卖出规则
# ---------------------------------------------------------------------------

class LHTProfitTargetRule(Rule):
    """止盈：最新≥近10日最高收盘×0.97时卖50%。"""

    @property
    def name(self) -> str:
        return "LHT止盈目标"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})
        history = stock.get("历史行情", [])

        latest = pankou.get("最新")
        if latest is None:
            return self._skip(f"{name}无最新价")

        try:
            latest_f = float(latest)
        except (TypeError, ValueError):
            return self._skip(f"{name}价格异常")

        if not history or not isinstance(history, list):
            return self._skip(f"{name}无历史行情")

        # 近10日最高收盘
        recent_closes = []
        for bar in history[-10:]:
            if isinstance(bar, dict):
                c = bar.get("收盘")
                if c is not None:
                    try:
                        recent_closes.append(float(c))
                    except (TypeError, ValueError):
                        pass

        if not recent_closes:
            return self._skip(f"{name}近10日收盘数据不足")

        max_close_10d = max(recent_closes)
        target = max_close_10d * 0.97

        if latest_f >= target:
            return self._fail(
                f"最新{latest_f:.2f}≥近10日最高收盘{max_close_10d:.2f}×97%={target:.2f}，触发止盈卖50%"
            )
        return self._pass(f"最新{latest_f:.2f}<止盈目标{target:.2f}，继续持有")


class LHTMAStopLossRule(Rule):
    """止损：最新收盘≤均线5日×0.98则次日开盘卖出。"""

    @property
    def name(self) -> str:
        return "LHT均线止损"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        tech = stock.get("技术指标", {})
        history = stock.get("历史行情", [])

        ma5 = tech.get("均线5日")
        if ma5 is None:
            return self._skip(f"{name}无MA5数据")

        try:
            ma5_f = float(ma5)
        except (TypeError, ValueError):
            return self._skip(f"{name}MA5格式异常")

        # 取最新收盘
        latest_close = None
        if history and isinstance(history, list) and isinstance(history[-1], dict):
            latest_close = history[-1].get("收盘")

        if latest_close is None:
            return self._skip(f"{name}无最新收盘价")

        try:
            close_f = float(latest_close)
        except (TypeError, ValueError):
            return self._skip(f"{name}收盘价格式异常")

        threshold = ma5_f * 0.98
        if close_f <= threshold:
            return self._fail(
                f"最新收盘{close_f:.2f}≤MA5×98%={threshold:.2f}，次日开盘应止损卖出"
            )
        return self._pass(f"最新收盘{close_f:.2f}>MA5×98%={threshold:.2f}，未触发止损")


class LHTTimeStopLossRule(Rule):
    """时间止损：持仓超5日K线且涨幅未超5%则清仓。"""

    def __init__(self, *, max_days: int = 5, min_gain: float = 5.0, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.max_days = max_days
        self.min_gain = min_gain

    @property
    def name(self) -> str:
        return "LHT时间止损"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        buy_price = stock.get("买入价")
        buy_time = stock.get("买入时间", "")
        history = stock.get("历史行情", [])
        pankou = stock.get("盘口", {})

        if not buy_price or not buy_time:
            return self._skip(f"{name}缺少买入价或买入时间")

        try:
            buy_f = float(buy_price)
        except (TypeError, ValueError):
            return self._skip(f"{name}买入价格式异常")

        # 估算持仓天数：通过历史行情中买入日之后的记录数
        if not history or not isinstance(history, list):
            return self._skip(f"{name}无历史行情")

        buy_date = str(buy_time)[:10]
        days_held = 0
        found_buy = False
        for bar in history:
            if not isinstance(bar, dict):
                continue
            bar_date = str(bar.get("日期", bar.get("date", "")))[:10]
            if bar_date == buy_date:
                found_buy = True
                continue
            if found_buy:
                days_held += 1

        if days_held < self.max_days:
            return self._pass(f"持仓{days_held}日<{self.max_days}日，未触发时间止损")

        # 检查涨幅
        latest = pankou.get("最新")
        if latest is None:
            if history and isinstance(history[-1], dict):
                latest = history[-1].get("收盘")

        if latest is None:
            return self._skip(f"{name}无最新价格")

        try:
            latest_f = float(latest)
        except (TypeError, ValueError):
            return self._skip(f"{name}最新价格式异常")

        if buy_f == 0:
            return self._skip("买入价为0")

        gain_pct = (latest_f - buy_f) / buy_f * 100

        if gain_pct < self.min_gain:
            return self._fail(
                f"持仓{days_held}日≥{self.max_days}日且涨幅{gain_pct:.2f}%<{self.min_gain}%，触发时间止损"
            )
        return self._pass(
            f"持仓{days_held}日≥{self.max_days}日但涨幅{gain_pct:.2f}%≥{self.min_gain}%，继续持有"
        )
