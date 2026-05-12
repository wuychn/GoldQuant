"""龙回头战法-买入规则（4条）。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


class LHTPriceNearMARule(Rule):
    """龙回头买入条件1：价格触及均线（最新价在任一均线的[99%, 102%]区间）。"""

    @property
    def name(self) -> str:
        return "LHT价格触及均线"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        code = str(stock.get("股票代码", "")).strip()
        pankou = stock.get("盘口", {})
        tech = stock.get("技术指标", {})

        latest = pankou.get("最新")
        if latest is None:
            return self._skip(f"{name}盘口无最新价")

        try:
            latest_f = float(latest)
        except (TypeError, ValueError):
            return self._skip(f"{name}最新价格式异常")

        ma_values = {}
        for label, key in [("5日", "均线5日"), ("10日", "均线10日"), ("20日", "均线20日")]:
            val = tech.get(key)
            if val is not None:
                try:
                    ma_values[label] = float(val)
                except (TypeError, ValueError):
                    pass

        if not ma_values:
            return self._fail(f"{name}({code})无均线数据")

        for label, ma_val in ma_values.items():
            if ma_val > 0:
                low = ma_val * 0.99
                high = ma_val * 1.02
                if low <= latest_f <= high:
                    return self._pass(
                        f"最新{latest_f:.2f}在均线{label}({ma_val:.2f})的"
                        f"[99%={low:.2f}, 102%={high:.2f}]区间内"
                    )

        ratios = ", ".join(f"{l}:{latest_f/v*100:.1f}%" for l, v in ma_values.items() if v > 0)
        return self._fail(f"最新{latest_f:.2f}不在任何均线[99%,102%]区间（{ratios}）")


class LHTTimeAfter10Rule(Rule):
    """龙回头买入条件2：买点时间≥10:00。"""

    @property
    def name(self) -> str:
        return "LHT买点时间"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        from datetime import datetime

        now = datetime.now()
        hour = now.hour
        minute = now.minute

        # 10:00 之后
        if hour > 10 or (hour == 10 and minute >= 0):
            return self._pass(f"当前时间{hour:02d}:{minute:02d}≥10:00")
        return self._fail(f"当前时间{hour:02d}:{minute:02d}<10:00，避开开盘不确定性")


class LHTVolumeRatioRule(Rule):
    """龙回头买入条件3：量比在[1.0, 3.0]。"""

    def __init__(self, *, min_ratio: float = 1.0, max_ratio: float = 3.0, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.min_ratio = min_ratio
        self.max_ratio = max_ratio

    @property
    def name(self) -> str:
        return "LHT盘中量比"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})

        ratio = pankou.get("量比")
        if ratio is None:
            return self._skip(f"{name}盘口无量比数据")

        try:
            ratio_f = float(ratio)
        except (TypeError, ValueError):
            return self._skip(f"{name}量比数据格式异常")

        if self.min_ratio <= ratio_f <= self.max_ratio:
            return self._pass(f"量比={ratio_f:.2f}在[{self.min_ratio},{self.max_ratio}]区间")
        if ratio_f > self.max_ratio:
            return self._fail(f"量比={ratio_f:.2f}>{self.max_ratio}，异动放弃")
        return self._fail(f"量比={ratio_f:.2f}<{self.min_ratio}，量能不足")


class LHTMainCapitalInflowRule(Rule):
    """龙回头买入条件4：主力资金做多（个股资金流最新主力净流入>0）。"""

    @property
    def name(self) -> str:
        return "LHT主力资金做多"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()

        capital_flow = stock.get("个股资金流", [])
        if not capital_flow or not isinstance(capital_flow, list):
            return self._skip(f"{name}无个股资金流数据")

        last_flow = capital_flow[-1] if isinstance(capital_flow[-1], dict) else {}
        net_amt = last_flow.get("主力净流入-净额", last_flow.get("净额"))

        if net_amt is None:
            return self._skip(f"{name}资金流无主力净流入数据")

        try:
            net_f = float(net_amt)
        except (TypeError, ValueError):
            return self._skip(f"{name}主力净流入数据格式异常")

        if net_f > 0:
            return self._pass(f"主力净流入={net_f:.0f}>0，资金做多")
        return self._fail(f"主力净流入={net_f:.0f}≤0，资金未做多")
