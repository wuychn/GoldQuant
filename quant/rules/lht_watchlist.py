"""龙回头战法-加自选规则（7条）。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


class LHTPopularityRule(Rule):
    """龙回头加自选条件1：人气排名≤50。"""

    def __init__(self, *, max_rank: int = 50, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.max_rank = max_rank

    @property
    def name(self) -> str:
        return "LHT人气排名"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()

        rank = stock.get("人气排名")
        if rank is None:
            pop = ctx.find_in_popularity(code)
            if pop:
                rank = pop.get("人气排名")

        if rank is None:
            return self._fail(f"{name}({code})无人气排名数据")

        try:
            rank = int(rank)
        except (TypeError, ValueError):
            return self._fail(f"{name}({code})人气排名数据异常: {rank}")

        if rank <= self.max_rank:
            return self._pass(f"人气排名={rank}≤{self.max_rank}")
        return self._fail(f"人气排名={rank}>{self.max_rank}")


class LHTRecentZTRule(Rule):
    """龙回头加自选条件2：近30日内曾涨停。"""

    def __init__(self, *, lookback_days: int = 30, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.lookback_days = lookback_days

    @property
    def name(self) -> str:
        return "LHT近期涨停"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()

        history = stock.get("历史行情", [])
        if not history:
            return self._fail(f"{name}({code})无历史行情数据")

        if not isinstance(history, list) or len(history) < 2:
            return self._fail(f"{name}({code})历史行情数据不足")

        # 创业板涨停阈值19.8%，其他9.8%
        threshold = 19.8 if code.startswith("30") else 9.8

        # 检查历史行情中是否有涨停日
        has_zt = False
        for i in range(1, min(len(history), self.lookback_days + 1)):
            bar = history[i] if i < len(history) else None
            if not isinstance(bar, dict):
                continue
            # 优先使用涨跌幅字段
            change = bar.get("涨跌幅")
            if change is not None:
                try:
                    if float(change) >= threshold:
                        has_zt = True
                        break
                except (TypeError, ValueError):
                    pass
            else:
                # 从相邻两日收盘计算
                prev_bar = history[i - 1] if i - 1 >= 0 and isinstance(history[i - 1], dict) else None
                if prev_bar:
                    try:
                        cur_close = float(bar.get("收盘", 0))
                        prev_close = float(prev_bar.get("收盘", 0))
                        if prev_close > 0:
                            pct = (cur_close - prev_close) / prev_close * 100
                            if pct >= threshold:
                                has_zt = True
                                break
                    except (TypeError, ValueError):
                        pass

        if has_zt:
            return self._pass(f"近{self.lookback_days}日内曾涨停")
        return self._fail(f"近{self.lookback_days}日内无涨停经历")


class LHTPullbackRule(Rule):
    """龙回头加自选条件3：回调幅度≥10%（近30日最高收盘到最新收盘）。"""

    def __init__(self, *, min_pullback: float = 10.0, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.min_pullback = min_pullback

    @property
    def name(self) -> str:
        return "LHT回调幅度"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()

        history = stock.get("历史行情", [])
        if not history or not isinstance(history, list):
            return self._fail(f"{name}({code})无历史行情数据")

        # 取近30日收盘价
        closes = []
        for bar in history[-30:]:
            if isinstance(bar, dict):
                c = bar.get("收盘")
                if c is not None:
                    try:
                        closes.append(float(c))
                    except (TypeError, ValueError):
                        pass

        if len(closes) < 5:
            return self._fail(f"{name}({code})收盘价数据不足")

        max_close = max(closes)
        latest_close = closes[-1]

        if max_close == 0:
            return self._skip("最高收盘价为0")

        pullback_pct = (max_close - latest_close) / max_close * 100

        if pullback_pct >= self.min_pullback:
            return self._pass(
                f"近30日最高{max_close:.2f}，最新{latest_close:.2f}，"
                f"回落{pullback_pct:.1f}%≥{self.min_pullback}%"
            )
        return self._fail(
            f"近30日最高{max_close:.2f}，最新{latest_close:.2f}，"
            f"回落{pullback_pct:.1f}%<{self.min_pullback}%，回调不充分"
        )


class LHTMASupportRule(Rule):
    """龙回头加自选条件4：回踩均线企稳（价格在任一均线的[98%,110%]且均线多头排列）。"""

    @property
    def name(self) -> str:
        return "LHT均线企稳"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        tech = stock.get("技术指标", {})

        # 获取均线数据
        ma5 = tech.get("均线5日")
        ma10 = tech.get("均线10日")
        ma20 = tech.get("均线20日")

        # 获取最新收盘价
        latest_close = tech.get("最新收盘价")
        if latest_close is None:
            history = stock.get("历史行情", [])
            if history and isinstance(history[-1], dict):
                latest_close = history[-1].get("收盘")

        if latest_close is None:
            return self._fail(f"{name}({code})无最新收盘价数据")

        try:
            price = float(latest_close)
        except (TypeError, ValueError):
            return self._fail(f"{name}({code})收盘价数据异常")

        # 检查价格在任一均线[98%, 110%]区间
        ma_values = {}
        for label, val in [("5日", ma5), ("10日", ma10), ("20日", ma20)]:
            if val is not None:
                try:
                    ma_values[label] = float(val)
                except (TypeError, ValueError):
                    pass

        if not ma_values:
            return self._fail(f"{name}({code})无均线数据")

        near_ma = None
        for label, ma_val in ma_values.items():
            if ma_val > 0:
                ratio = price / ma_val
                if 0.98 <= ratio <= 1.10:
                    near_ma = (label, ma_val, ratio)
                    break

        if near_ma is None:
            ratios_info = ", ".join(
                f"{l}={price/v*100:.1f}%" for l, v in ma_values.items() if v > 0
            )
            return self._fail(f"{name}({code})不在任何均线[98%,110%]区间（{ratios_info}）")

        # 检查均线多头排列（至少满足其一）
        ma5_v = ma_values.get("5日", 0)
        ma10_v = ma_values.get("10日", 0)
        ma20_v = ma_values.get("20日", 0)

        bullish = False
        if ma5_v > 0 and ma10_v > 0 and ma5_v >= ma10_v:
            bullish = True
        elif ma10_v > 0 and ma20_v > 0 and ma10_v >= ma20_v:
            bullish = True

        if not bullish:
            return self._fail(
                f"{name}({code})均线非多头排列（MA5={ma5_v:.2f}, MA10={ma10_v:.2f}, MA20={ma20_v:.2f}）"
            )

        label, ma_val, ratio = near_ma
        return self._pass(
            f"收盘{price:.2f}在均线{label}({ma_val:.2f})的{ratio*100:.1f}%处，均线多头排列"
        )


class LHTConsecutiveUpRule(Rule):
    """龙回头加自选条件5：连续两日收阳（收盘>开盘）。"""

    @property
    def name(self) -> str:
        return "LHT连续收阳"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()

        history = stock.get("历史行情", [])
        if not history or not isinstance(history, list) or len(history) < 2:
            return self._fail(f"{name}({code})历史行情不足2日")

        last_two = history[-2:]
        all_positive = True
        for bar in last_two:
            if not isinstance(bar, dict):
                all_positive = False
                break
            try:
                close = float(bar.get("收盘", 0))
                open_p = float(bar.get("开盘", 0))
                if close <= open_p:
                    all_positive = False
                    break
            except (TypeError, ValueError):
                all_positive = False
                break

        if all_positive:
            return self._pass("最后两日均收阳（收盘>开盘）")
        return self._fail("最后两日未连续收阳")


class LHTVolumeRule(Rule):
    """龙回头加自选条件6：量能温和放大（最近一日成交额在前5日均值的[1.0, 3.0]倍）。"""

    def __init__(self, *, min_ratio: float = 1.0, max_ratio: float = 3.0, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.min_ratio = min_ratio
        self.max_ratio = max_ratio

    @property
    def name(self) -> str:
        return "LHT量能放大"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()

        history = stock.get("历史行情", [])
        if not history or not isinstance(history, list) or len(history) < 6:
            return self._fail(f"{name}({code})历史行情不足6日（需最近1日+前5日均值）")

        # 取最近6日成交额
        amounts = []
        for bar in history[-6:]:
            if isinstance(bar, dict):
                amt = bar.get("成交额", bar.get("金额"))
                if amt is not None:
                    try:
                        amounts.append(float(amt))
                    except (TypeError, ValueError):
                        pass

        if len(amounts) < 6:
            return self._fail(f"{name}({code})成交额数据不足")

        latest_amt = amounts[-1]
        prev5_avg = sum(amounts[-6:-1]) / 5

        if prev5_avg == 0:
            return self._skip("前5日成交额均值为0")

        ratio = latest_amt / prev5_avg

        if self.min_ratio <= ratio <= self.max_ratio:
            return self._pass(
                f"最近成交额{latest_amt:.0f}/前5日均值{prev5_avg:.0f}={ratio:.2f}倍，"
                f"在[{self.min_ratio},{self.max_ratio}]区间"
            )
        return self._fail(
            f"量比{ratio:.2f}倍不在[{self.min_ratio},{self.max_ratio}]区间"
            f"（最近{latest_amt:.0f}/均值{prev5_avg:.0f}）"
        )


class LHTMACDRule(Rule):
    """龙回头加自选条件7：MACD配合（DIF>DEA且DIF>0）。"""

    @property
    def name(self) -> str:
        return "LHT_MACD配合"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        tech = stock.get("技术指标", {})
        macd = tech.get("MACD", {})

        dif = macd.get("差离值", macd.get("DIF"))
        dea = macd.get("信号线", macd.get("DEA"))

        if dif is None or dea is None:
            return self._fail(f"{name}({code})MACD数据缺失")

        try:
            dif_f = float(dif)
            dea_f = float(dea)
        except (TypeError, ValueError):
            return self._fail(f"{name}({code})MACD数据格式异常")

        if dif_f > dea_f and dif_f > 0:
            return self._pass(f"DIF={dif_f:.4f}>DEA={dea_f:.4f}且DIF>0，多头趋势")
        reasons = []
        if dif_f <= dea_f:
            reasons.append(f"DIF={dif_f:.4f}≤DEA={dea_f:.4f}")
        if dif_f <= 0:
            reasons.append(f"DIF={dif_f:.4f}≤0")
        return self._fail("；".join(reasons))
