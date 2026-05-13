"""涨停板战法-持股监控 + 卖出规则。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


# ---------------------------------------------------------------------------
# 持股监控
# ---------------------------------------------------------------------------

class ZTAboveAvgHoldRule(Rule):
    """持股监控：分时均线上方运行（最新≥均价）。"""

    @property
    def name(self) -> str:
        return "ZT分时强势持有"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})

        latest = pankou.get("最新")
        avg_price = pankou.get("均价")

        if latest is None or avg_price is None:
            return self._skip(f"{name}盘口缺失最新/均价")

        try:
            latest_f = float(latest)
            avg_f = float(avg_price)
        except (TypeError, ValueError):
            return self._skip(f"{name}价格数据格式异常")

        if latest_f >= avg_f:
            return self._pass(f"最新{latest_f:.2f}≥均价{avg_f:.2f}，强势持有")
        return self._fail(f"最新{latest_f:.2f}<均价{avg_f:.2f}，分时走弱")


class ZTWeaknessOutflowRule(Rule):
    """卖出信号（条件1+2组合）：分时走弱 + 资金流出。"""

    @property
    def name(self) -> str:
        return "ZT走弱+资金流出"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})

        latest = pankou.get("最新")
        avg_price = pankou.get("均价")

        if latest is None or avg_price is None:
            return self._skip(f"{name}盘口数据缺失")

        try:
            latest_f = float(latest)
            avg_f = float(avg_price)
        except (TypeError, ValueError):
            return self._skip(f"{name}数据格式异常")

        below_avg = latest_f < avg_f

        # 检查资金流出
        capital_flow = stock.get("个股资金流", [])
        net_outflow = False
        if isinstance(capital_flow, list) and capital_flow:
            last_flow = capital_flow[-1] if isinstance(capital_flow[-1], dict) else {}
            net_amt = last_flow.get("主力净流入-净额", last_flow.get("净额", 0))
            try:
                net_outflow = float(net_amt) < 0
            except (TypeError, ValueError):
                pass

        if below_avg and net_outflow:
            return self._fail(
                f"分时走弱（最新{latest_f:.2f}<均价{avg_f:.2f}）且主力净流出，触发减仓信号",
                sell_type="减半",
            )

        if below_avg:
            return self._pass(f"分时偏弱但资金未明显流出，暂持有观察")
        return self._pass(f"分时强势，正常持有")


class ZTProfitPullbackRule(Rule):
    """卖出信号（条件3）：利润回撤（浮盈≥5%后从最高回落≥4%）。"""

    def __init__(self, *, profit_trigger: float = 5.0, pullback_trigger: float = 4.0, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.profit_trigger = profit_trigger
        self.pullback_trigger = pullback_trigger

    @property
    def name(self) -> str:
        return "ZT利润回撤止盈"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})

        latest = pankou.get("最新")
        highest = pankou.get("最高")
        buy_price = stock.get("买入价")

        if latest is None or buy_price is None:
            return self._skip(f"{name}缺失最新价或买入价")

        try:
            latest_f = float(latest)
            buy_f = float(buy_price)
            high_f = float(highest) if highest else latest_f
        except (TypeError, ValueError):
            return self._skip(f"{name}价格数据异常")

        if buy_f == 0:
            return self._skip("买入价为0")

        # 计算浮盈
        profit_pct = (high_f - buy_f) / buy_f * 100

        if profit_pct < self.profit_trigger:
            return self._pass(f"最高浮盈{profit_pct:.2f}%未达{self.profit_trigger}%触发线")

        # 浮盈已达触发线，检查回撤
        if high_f == 0:
            return self._skip("最高价为0")

        pullback_pct = (high_f - latest_f) / high_f * 100

        if pullback_pct >= self.pullback_trigger:
            return self._fail(
                f"浮盈曾达{profit_pct:.2f}%≥{self.profit_trigger}%，"
                f"从最高{high_f:.2f}回落{pullback_pct:.2f}%≥{self.pullback_trigger}%，触发移动止盈",
                sell_type="止盈",
            )
        return self._pass(
            f"浮盈{profit_pct:.2f}%，从最高回落{pullback_pct:.2f}%<{self.pullback_trigger}%，继续持有"
        )


# ---------------------------------------------------------------------------
# 开盘形态卖出
# ---------------------------------------------------------------------------

class ZTOpenPatternSellRule(Rule):
    """按开盘形态设条件单。"""

    @property
    def name(self) -> str:
        return "ZT开盘形态卖出"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})

        today_open = pankou.get("今开")
        yesterday_close = pankou.get("昨收")
        limit_up = pankou.get("涨停")
        latest = pankou.get("最新")
        highest = pankou.get("最高")

        if not all([today_open, yesterday_close, latest]):
            return self._skip(f"{name}盘口数据不全")

        try:
            open_f = float(today_open)
            close_f = float(yesterday_close)
            limit_f = float(limit_up) if limit_up else 0
            latest_f = float(latest)
            high_f = float(highest) if highest else latest_f
        except (TypeError, ValueError):
            return self._skip(f"{name}数据格式异常")

        if close_f == 0:
            return self._skip("昨收为0")

        # 判断开盘形态
        buy1_qty = pankou.get("买1量", pankou.get("买一量", 0))
        try:
            buy1_qty = float(buy1_qty)
        except (TypeError, ValueError):
            buy1_qty = 0

        if limit_f > 0 and abs(open_f - limit_f) < 0.001 and buy1_qty > 0:
            # 一字涨停开
            pattern = "一字涨停开"
            pullback = (high_f - latest_f) / high_f * 100 if high_f > 0 else 0
            if pullback >= 4:
                return self._fail(f"{pattern}：从最高回落{pullback:.2f}%≥4%，建议卖50%", sell_type="减半")
            below_open = latest_f < open_f
            if below_open:
                return self._fail(f"{pattern}：最新{latest_f:.2f}<今开{open_f:.2f}，建议清仓", sell_type="清仓")
            return self._pass(f"{pattern}：暂未触发卖出条件")

        gap_pct = (open_f - close_f) / close_f * 100

        if gap_pct >= 2:
            # 高开
            pattern = "高开"
            if latest_f < open_f:
                return self._fail(f"{pattern}（+{gap_pct:.1f}%）：最新{latest_f:.2f}<今开{open_f:.2f}，建议卖50%", sell_type="减半")
            pullback_from_high = (high_f - latest_f) / high_f * 100 if high_f > 0 else 0
            if pullback_from_high >= 5.5:
                return self._fail(f"{pattern}：从最高回落{pullback_from_high:.2f}%≥5.5%，建议清仓", sell_type="清仓")
            return self._pass(f"{pattern}：暂未触发卖出条件")

        if 0.5 <= gap_pct < 2:
            # 普通开
            pattern = "普通开"
            avg_price = pankou.get("均价")
            try:
                avg_f = float(avg_price) if avg_price else close_f
            except (TypeError, ValueError):
                avg_f = close_f
            if latest_f < avg_f:
                return self._fail(f"{pattern}：最新{latest_f:.2f}<均价{avg_f:.2f}，建议卖50%", sell_type="减半")
            if latest_f < close_f:
                return self._fail(f"{pattern}：最新{latest_f:.2f}<昨收{close_f:.2f}，建议清仓", sell_type="清仓")
            return self._pass(f"{pattern}：暂未触发卖出条件")

        # 低开
        pattern = "低开"
        drop_pct = (latest_f - close_f) / close_f * 100
        if drop_pct <= -8:
            return self._fail(f"{pattern}：跌幅{drop_pct:.2f}%达-8%，建议一次清仓", sell_type="清仓")
        return self._pass(f"{pattern}：跌幅{drop_pct:.2f}%，暂未触发")


class ZTATRStopLossRule(Rule):
    """ATR止损：止损价=买入价-min(2×ATR14, 买入价×7%)，创业板12%。"""

    @property
    def name(self) -> str:
        return "ZT_ATR止损"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        code = str(stock.get("股票代码", "")).strip()
        pankou = stock.get("盘口", {})
        tech = stock.get("技术指标", {})

        latest = pankou.get("最新")
        buy_price = stock.get("买入价")
        atr14 = tech.get("ATR14")

        if latest is None or buy_price is None:
            return self._skip(f"{name}缺失最新价或买入价")

        try:
            latest_f = float(latest)
            buy_f = float(buy_price)
        except (TypeError, ValueError):
            return self._skip(f"{name}价格数据异常")

        if buy_f == 0:
            return self._skip("买入价为0")

        # 创业板用12%，其他用7%
        max_loss_pct = 0.12 if code.startswith("30") else 0.07

        if atr14 is not None:
            try:
                atr_f = float(atr14)
                stop_loss = buy_f - min(2 * atr_f, buy_f * max_loss_pct)
            except (TypeError, ValueError):
                stop_loss = buy_f * (1 - max_loss_pct)
        else:
            stop_loss = buy_f * (1 - max_loss_pct)

        if latest_f <= stop_loss:
            return self._fail(
                f"最新{latest_f:.2f}≤ATR止损价{stop_loss:.2f}（买入价{buy_f:.2f}），触发止损",
                sell_type="止损", stop_loss_price=stop_loss,
            )
        return self._pass(f"最新{latest_f:.2f}>止损价{stop_loss:.2f}，安全")


class ZTTimeStopLossRule(Rule):
    """时间止损：14:55 最新<今开 且振幅<3% 且涨幅<1% → 清仓。"""

    @property
    def name(self) -> str:
        return "ZT时间止损"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})

        latest = pankou.get("最新")
        today_open = pankou.get("今开")
        highest = pankou.get("最高")
        lowest = pankou.get("最低")
        yesterday_close = pankou.get("昨收")
        gain = pankou.get("涨幅")

        if not all([latest, today_open, highest, lowest, yesterday_close]):
            return self._skip(f"{name}盘口数据不全")

        try:
            latest_f = float(latest)
            open_f = float(today_open)
            high_f = float(highest)
            low_f = float(lowest)
            close_f = float(yesterday_close)
            gain_f = float(gain) if gain is not None else 0
        except (TypeError, ValueError):
            return self._skip(f"{name}数据格式异常")

        if close_f == 0:
            return self._skip("昨收为0")

        # 振幅
        amplitude = (high_f - low_f) / close_f * 100

        below_open = latest_f < open_f
        low_amplitude = amplitude < 3
        low_gain = gain_f < 1

        if below_open and low_amplitude and low_gain:
            return self._fail(
                f"时间止损条件满足：最新{latest_f:.2f}<今开{open_f:.2f}，"
                f"振幅{amplitude:.2f}%<3%，涨幅{gain_f:.2f}%<1%，14:55应清仓",
                sell_type="时间止损",
            )
        return self._pass(
            f"时间止损未触发（最新{'<' if below_open else '≥'}今开，振幅{amplitude:.2f}%，涨幅{gain_f:.2f}%）"
        )
