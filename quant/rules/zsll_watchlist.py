"""主升浪战法-加自选规则：中长期趋势与结构，参数均由 rules_config 覆盖。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


class ZSLPopularityRule(Rule):
    """主升浪：人气排名≤上限。"""

    def default_params(self) -> dict:
        return {"max_rank": 80}

    @property
    def name(self) -> str:
        return "ZSL人气排名"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        max_rank = int(self.params["max_rank"])

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

        if rank <= max_rank:
            return self._pass(f"人气排名={rank}≤{max_rank}")
        return self._fail(f"人气排名={rank}>{max_rank}")


class ZSLTrendGainRule(Rule):
    """主升浪：近 ref_days 根 K 线累计涨幅≥阈值（刻画中期上行）。"""

    def default_params(self) -> dict:
        return {"ref_days": 15, "min_gain_pct": 5.0}

    @property
    def name(self) -> str:
        return "ZSL中期涨幅"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        ref = int(self.params["ref_days"])
        min_gain = float(self.params["min_gain_pct"])

        history = stock.get("历史行情", [])
        if not history or not isinstance(history, list):
            return self._fail(f"{name}({code})无历史行情数据")

        closes: list[float] = []
        for bar in history:
            if isinstance(bar, dict):
                c = bar.get("收盘")
                if c is not None:
                    try:
                        closes.append(float(c))
                    except (TypeError, ValueError):
                        pass

        if len(closes) < ref + 1:
            return self._fail(f"{name}({code})收盘数据不足{ref+1}根")

        base = closes[-(ref + 1)]
        latest = closes[-1]
        if base <= 0:
            return self._skip("基准收盘无效")

        gain_pct = (latest - base) / base * 100
        if gain_pct >= min_gain:
            return self._pass(
                f"近{ref}个交易日累计涨幅{gain_pct:.2f}%≥{min_gain}%（{base:.2f}→{latest:.2f}）"
            )
        return self._fail(
            f"近{ref}个交易日累计涨幅{gain_pct:.2f}%<{min_gain}%，中期趋势不足"
        )


class ZSLMaLongTrendRule(Rule):
    """主升浪：均线多头 + 收盘相对 MA5/MA20 位置（可配）。"""

    def default_params(self) -> dict:
        return {
            "require_full_stack": True,
            "ma5_floor_ratio": 0.98,
            "ma20_floor_ratio": 1.0,
        }

    @property
    def name(self) -> str:
        return "ZSL均线趋势"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        full_stack = bool(self.params["require_full_stack"])
        f5 = float(self.params["ma5_floor_ratio"])
        f20 = float(self.params["ma20_floor_ratio"])

        tech = stock.get("技术指标", {})
        ma5 = tech.get("均线5日")
        ma10 = tech.get("均线10日")
        ma20 = tech.get("均线20日")
        latest_close = tech.get("最新收盘价")
        if latest_close is None:
            history = stock.get("历史行情", [])
            if history and isinstance(history[-1], dict):
                latest_close = history[-1].get("收盘")

        if latest_close is None:
            return self._fail(f"{name}({code})无最新收盘价数据")

        try:
            price = float(latest_close)
            ma5_f = float(ma5) if ma5 is not None else 0.0
            ma10_f = float(ma10) if ma10 is not None else 0.0
            ma20_f = float(ma20) if ma20 is not None else 0.0
        except (TypeError, ValueError):
            return self._fail(f"{name}({code})均线或收盘数据异常")

        if ma5_f <= 0 or ma10_f <= 0 or ma20_f <= 0:
            return self._fail(f"{name}({code})均线数据不全")

        if full_stack:
            ok_stack = ma5_f >= ma10_f >= ma20_f
        else:
            ok_stack = (ma5_f >= ma10_f) or (ma10_f >= ma20_f)

        if not ok_stack:
            return self._fail(
                f"{name}({code})均线结构未满足（MA5={ma5_f:.2f}, MA10={ma10_f:.2f}, MA20={ma20_f:.2f}）"
            )

        if price < ma5_f * f5:
            return self._fail(f"{name}({code})收盘{price:.2f}<MA5×{f5}={ma5_f*f5:.2f}")
        if price < ma20_f * f20:
            return self._fail(f"{name}({code})收盘{price:.2f}<MA20×{f20}={ma20_f*f20:.2f}")

        return self._pass(
            f"收盘{price:.2f}，MA5≥MA10≥MA20 且站在 MA5/MA20 强势区之上"
        )


class ZSLWindowMaxDrawdownRule(Rule):
    """主升浪：回看窗口内自高点最大回撤不超过阈值（允许趋势中的正常洗盘）。"""

    def default_params(self) -> dict:
        return {"lookback_days": 60, "max_drawdown_from_high_pct": 35.0}

    @property
    def name(self) -> str:
        return "ZSL阶段回撤"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        lookback = int(self.params["lookback_days"])
        max_dd = float(self.params["max_drawdown_from_high_pct"])

        history = stock.get("历史行情", [])
        if not history or not isinstance(history, list):
            return self._fail(f"{name}({code})无历史行情数据")

        closes: list[float] = []
        for bar in history[-lookback:]:
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
        if max_close <= 0:
            return self._skip("最高价无效")

        dd_pct = (max_close - latest_close) / max_close * 100
        if dd_pct <= max_dd:
            return self._pass(
                f"近{lookback}日内自高点的回撤{dd_pct:.1f}%≤{max_dd}%（趋势未破坏）"
            )
        return self._fail(
            f"近{lookback}日内自高点回撤{dd_pct:.1f}%>{max_dd}%，中期结构偏弱"
        )


class ZSLPositiveDaysRatioRule(Rule):
    """主升浪：近 window 根 K 线中收阳占比≥阈值。"""

    def default_params(self) -> dict:
        return {"window_days": 15, "min_positive_ratio": 0.52}

    @property
    def name(self) -> str:
        return "ZSL收阳占比"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        window = int(self.params["window_days"])
        min_r = float(self.params["min_positive_ratio"])

        history = stock.get("历史行情", [])
        if not history or not isinstance(history, list) or len(history) < window:
            return self._fail(f"{name}({code})历史行情不足{window}日")

        slice_ = history[-window:]
        up = 0
        for bar in slice_:
            if not isinstance(bar, dict):
                continue
            try:
                close = float(bar.get("收盘", 0))
                open_p = float(bar.get("开盘", 0))
                if close > open_p:
                    up += 1
            except (TypeError, ValueError):
                pass

        ratio = up / window if window else 0.0
        if ratio >= min_r:
            return self._pass(f"近{window}日中收阳{up}天，占比{ratio*100:.1f}%≥{min_r*100:.0f}%")
        return self._fail(f"近{window}日收阳占比{ratio*100:.1f}%<{min_r*100:.0f}%，多头氛围不足")


class ZSLVolumeThrustRule(Rule):
    """主升浪：成交额相对前5日均值处于活跃区间。"""

    def default_params(self) -> dict:
        return {"min_ratio": 0.9, "max_ratio": 5.0}

    @property
    def name(self) -> str:
        return "ZSL量能活跃"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        min_ratio = float(self.params["min_ratio"])
        max_ratio = float(self.params["max_ratio"])

        history = stock.get("历史行情", [])
        if not history or not isinstance(history, list) or len(history) < 6:
            return self._fail(f"{name}({code})历史行情不足6日（需最近1日+前5日均值）")

        amounts: list[float] = []
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

        if min_ratio <= ratio <= max_ratio:
            return self._pass(
                f"最近成交额/前5日均值={ratio:.2f}倍，在[{min_ratio},{max_ratio}]区间"
            )
        return self._fail(
            f"成交额倍率{ratio:.2f}不在[{min_ratio},{max_ratio}]区间"
        )


class ZSLMACDRule(Rule):
    """主升浪：MACD 多头。"""

    @property
    def name(self) -> str:
        return "ZSL_MACD配合"

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
