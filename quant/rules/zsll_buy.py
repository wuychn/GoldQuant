"""主升浪战法-买入规则（沿趋势强势，与龙回头「回踩均线」区分）。"""

from __future__ import annotations

from datetime import datetime

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


class ZSLPriceRidingMARule(Rule):
    """主升浪买入条件1：沿均线强势区——贴近或略高于短期均线。"""

    def default_params(self) -> dict:
        return {
            "ma5_low_ratio": 0.99,
            "ma5_high_ratio": 1.06,
            "ma10_low_ratio": 0.99,
            "ma10_high_ratio": 1.03,
            "ma20_low_ratio": 0.99,
            "ma20_high_ratio": 1.03,
        }

    @property
    def name(self) -> str:
        return "ZSL沿均线强势"

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

        ma5_lo = float(self.params["ma5_low_ratio"])
        ma5_hi = float(self.params["ma5_high_ratio"])
        ma10_lo = float(self.params["ma10_low_ratio"])
        ma10_hi = float(self.params["ma10_high_ratio"])
        ma20_lo = float(self.params["ma20_low_ratio"])
        ma20_hi = float(self.params["ma20_high_ratio"])

        ma_values: dict[str, tuple[float, float, float]] = {}
        for label, key in [("5日", "均线5日"), ("10日", "均线10日"), ("20日", "均线20日")]:
            val = tech.get(key)
            if val is not None:
                try:
                    v = float(val)
                    if v > 0:
                        if label == "5日":
                            ma_values[label] = (v * ma5_lo, v * ma5_hi, v)
                        elif label == "10日":
                            ma_values[label] = (v * ma10_lo, v * ma10_hi, v)
                        else:
                            ma_values[label] = (v * ma20_lo, v * ma20_hi, v)
                except (TypeError, ValueError):
                    pass

        if not ma_values:
            return self._fail(f"{name}({code})无均线数据")

        for label, (low, high, ma_val) in ma_values.items():
            if low <= latest_f <= high:
                return self._pass(
                    f"最新{latest_f:.2f}在均线{label}({ma_val:.2f})强势区"
                    f"[{low:.2f},{high:.2f}]"
                )

        ratios = ", ".join(
            f"{l}:{latest_f/t[2]*100:.1f}%" for l, t in ma_values.items()
        )
        return self._fail(f"最新{latest_f:.2f}不在任一均线强势区（{ratios}）")


class ZSLTimeAfter10Rule(Rule):
    """主升浪买入条件2：买点时间≥10:00。"""

    def default_params(self) -> dict:
        return {"earliest_hour": 10, "earliest_minute": 0}

    @property
    def name(self) -> str:
        return "ZSL买点时间"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        now = datetime.now()
        hour = now.hour
        minute = now.minute
        eh = int(self.params["earliest_hour"])
        em = int(self.params["earliest_minute"])

        if hour > eh or (hour == eh and minute >= em):
            return self._pass(f"当前时间{hour:02d}:{minute:02d}≥{eh:02d}:{em:02d}")
        return self._fail(f"当前时间{hour:02d}:{minute:02d}<{eh:02d}:{em:02d}，避开开盘不确定性")


class ZSLVolumeRatioRule(Rule):
    """主升浪买入条件3：量比区间（略宽于龙回头以容纳放量上攻）。"""

    def default_params(self) -> dict:
        return {"min_ratio": 1.0, "max_ratio": 3.5}

    @property
    def name(self) -> str:
        return "ZSL盘中量比"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})
        min_r = float(self.params["min_ratio"])
        max_r = float(self.params["max_ratio"])

        ratio = pankou.get("量比")
        if ratio is None:
            return self._skip(f"{name}盘口无量比数据")

        try:
            ratio_f = float(ratio)
        except (TypeError, ValueError):
            return self._skip(f"{name}量比数据格式异常")

        if min_r <= ratio_f <= max_r:
            return self._pass(f"量比={ratio_f:.2f}在[{min_r},{max_r}]区间")
        if ratio_f > max_r:
            return self._fail(f"量比={ratio_f:.2f}>{max_r}，异动放弃")
        return self._fail(f"量比={ratio_f:.2f}<{min_r}，量能不足")


class ZSLMainCapitalInflowRule(Rule):
    """主升浪买入条件4：主力资金净流入>0。"""

    @property
    def name(self) -> str:
        return "ZSL主力资金做多"

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
