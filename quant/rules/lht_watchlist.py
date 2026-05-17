"""龙回头战法-加自选规则：前期连板辨识度 + 深调企稳第二波，参数均由 rules_config 覆盖。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext
from quant.rules.daily_bar_zt import max_consecutive_limit_up_days
from quant.rules.lht_dragon import evaluate_lht_dragon_watchlist


class LHTPopularityRule(Rule):
    """龙回头加自选：人气排名≤上限。"""

    def default_params(self) -> dict:
        return {"max_rank": 50}

    @property
    def name(self) -> str:
        return "LHT人气排名"

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


class LHTMaxConsecutiveZTRunRule(Rule):
    """龙回头加自选：回看窗口内「连续涨停」最大天数≥阈值（刻画前期连板龙头）。"""

    def default_params(self) -> dict:
        return {
            "lookback_days": 30,
            "min_consecutive_zt_days": 2,
            "main_board_zt_pct": 9.8,
            "cyb_zt_pct": 19.8,
        }

    @property
    def name(self) -> str:
        return "LHT前期连板"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        lookback = int(self.params["lookback_days"])
        need = int(self.params["min_consecutive_zt_days"])
        main_pct = float(self.params["main_board_zt_pct"])
        cyb_pct = float(self.params["cyb_zt_pct"])

        history = stock.get("历史行情", [])
        if not history or not isinstance(history, list) or len(history) < 2:
            return self._fail(f"{name}({code})历史行情不足")

        best = max_consecutive_limit_up_days(
            history,
            code,
            lookback_days=lookback,
            main_pct=main_pct,
            cyb_pct=cyb_pct,
            exclude_one_word=bool(self.params.get("exclude_one_word_from_zt_run", True)),
            one_word_amp_pct_max=float(self.params.get("one_word_amp_pct_max", 0.12)),
        )

        if best >= need:
            return self._pass(
                f"近{lookback}日内最大连续涨停{best}天≥要求{need}天"
            )
        return self._fail(
            f"近{lookback}日内最大连续涨停{best}天<{need}天，前期连板辨识度不足"
        )


class LHTPullbackRule(Rule):
    """龙回头加自选：从阶段高点回落幅度≥阈值（深调）。"""

    def default_params(self) -> dict:
        return {"min_pullback_pct": 10.0, "lookback_days": 30}

    @property
    def name(self) -> str:
        return "LHT回调幅度"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        min_pb = float(self.params["min_pullback_pct"])
        lookback = int(self.params["lookback_days"])

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

        if max_close == 0:
            return self._skip("最高收盘价为0")

        pullback_pct = (max_close - latest_close) / max_close * 100

        if pullback_pct >= min_pb:
            return self._pass(
                f"近{lookback}日最高{max_close:.2f}，最新{latest_close:.2f}，"
                f"回落{pullback_pct:.1f}%≥{min_pb}%"
            )
        return self._fail(
            f"近{lookback}日最高{max_close:.2f}，最新{latest_close:.2f}，"
            f"回落{pullback_pct:.1f}%<{min_pb}%，深调不充分"
        )


class LHTMASupportRule(Rule):
    """龙回头加自选：回踩均线企稳 + 均线多头（区间与多头要求可配）。"""

    def default_params(self) -> dict:
        return {
            "ma_low_ratio": 0.98,
            "ma_high_ratio": 1.10,
            "require_ma5_ge_ma10": False,
        }

    @property
    def name(self) -> str:
        return "LHT均线企稳"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        lo = float(self.params["ma_low_ratio"])
        hi = float(self.params["ma_high_ratio"])
        require55 = bool(self.params["require_ma5_ge_ma10"])

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
        except (TypeError, ValueError):
            return self._fail(f"{name}({code})收盘价数据异常")

        ma_values: dict[str, float] = {}
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
                if lo <= ratio <= hi:
                    near_ma = (label, ma_val, ratio)
                    break

        if near_ma is None:
            ratios_info = ", ".join(
                f"{l}={price/v*100:.1f}%" for l, v in ma_values.items() if v > 0
            )
            return self._fail(
                f"{name}({code})不在任何均线[{lo*100:.0f}%,{hi*100:.0f}%]区间（{ratios_info}）"
            )

        ma5_v = ma_values.get("5日", 0)
        ma10_v = ma_values.get("10日", 0)
        ma20_v = ma_values.get("20日", 0)

        bullish = False
        if require55:
            if ma5_v > 0 and ma10_v > 0 and ma20_v > 0:
                bullish = ma5_v >= ma10_v >= ma20_v
        else:
            if ma5_v > 0 and ma10_v > 0 and ma5_v >= ma10_v:
                bullish = True
            elif ma10_v > 0 and ma20_v > 0 and ma10_v >= ma20_v:
                bullish = True

        if not bullish:
            return self._fail(
                f"{name}({code})均线多头未满足（MA5={ma5_v:.2f}, MA10={ma10_v:.2f}, MA20={ma20_v:.2f}）"
            )

        label, ma_val, ratio = near_ma
        return self._pass(
            f"收盘{price:.2f}在均线{label}({ma_val:.2f})的{ratio*100:.1f}%处，多头企稳"
        )


class LHTConsecutiveUpRule(Rule):
    """龙回头加自选：连续收阳企稳。"""

    def default_params(self) -> dict:
        return {"min_consecutive_days": 2}

    @property
    def name(self) -> str:
        return "LHT连续收阳"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        n = int(self.params["min_consecutive_days"])
        if n < 1:
            n = 1

        history = stock.get("历史行情", [])
        if not history or not isinstance(history, list) or len(history) < n:
            return self._fail(f"{name}({code})历史行情不足{n}日")

        last_n = history[-n:]
        for bar in last_n:
            if not isinstance(bar, dict):
                return self._fail(f"{name}({code})K线数据异常")
            try:
                close = float(bar.get("收盘", 0))
                open_p = float(bar.get("开盘", 0))
                if close <= open_p:
                    return self._fail(f"{name}({code})近{n}日未连续收阳")
            except (TypeError, ValueError):
                return self._fail(f"{name}({code})价格格式异常")

        return self._pass(f"最近{n}日均收阳（收盘>开盘）")


class LHTVolumeRule(Rule):
    """龙回头加自选：量能温和放大。"""

    def default_params(self) -> dict:
        return {"min_ratio": 1.0, "max_ratio": 3.0}

    @property
    def name(self) -> str:
        return "LHT量能放大"

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
                f"最近成交额{latest_amt:.0f}/前5日均值{prev5_avg:.0f}={ratio:.2f}倍，"
                f"在[{min_ratio},{max_ratio}]区间"
            )
        return self._fail(
            f"量比{ratio:.2f}倍不在[{min_ratio},{max_ratio}]区间"
            f"（最近{latest_amt:.0f}/均值{prev5_avg:.0f}）"
        )


class LHTMACDRule(Rule):
    """龙回头加自选：MACD 多头配合。"""

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


def _hist_bar_date(bar: dict) -> str:
    raw = bar.get("日期", bar.get("date", ""))
    s = str(raw).strip().replace("/", "-")
    return s[:10] if len(s) >= 10 else s


class LHTDragonReturnWatchlistRule(Rule):
    """龙回头：≥N 连板(T0)+固定观察日内出现单日急跌/急涨，禁阴跌、禁有效破位 MA(D)。"""

    def default_params(self) -> dict:
        return {
            "lookback_days": 120,
            "observation_days": 30,
            "min_zt_run_days": 5,
            "main_board_zt_pct": 9.8,
            "cyb_zt_pct": 19.8,
            "exclude_one_word_from_zt_run": True,
            "one_word_amp_pct_max": 0.12,
            "sharp_down_pct": 7.0,
            "sharp_up_pct": 7.0,
            "yin_die_min_consecutive_bearish_bars": 4,
            "ma_window": 20,
            "effective_break_below_ma_consecutive_days": 2,
        }

    @property
    def name(self) -> str:
        return "LHT龙回头形态"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        history = stock.get("历史行情", [])

        ok, detail, extras = evaluate_lht_dragon_watchlist(
            history,
            code,
            self.params,
        )
        if not ok:
            return self._fail(detail)

        od = int(self.params.get("observation_days", 30))
        t0idx_raw = extras.get("T0_index")
        meta: dict = {
            "股票代码": code,
            "股票名称": name,
            "战法细分": "龙回头战法·观察入库",
        }
        if t0idx_raw is not None and isinstance(history, list):
            t0idx = int(t0idx_raw)
            if 0 <= t0idx < len(history) and isinstance(history[t0idx], dict):
                meta["观察T0日期"] = _hist_bar_date(history[t0idx])
            last_i = min(len(history) - 1, t0idx + od - 1)
            if 0 <= last_i < len(history) and isinstance(history[last_i], dict):
                meta["观察到期日锚点"] = _hist_bar_date(history[last_i])
            meta["观察说明"] = (
                f"最后一轮非一字连板后首根非涨停日(bar#{t0idx})起{od}个交易日内；"
                f"急跌后出现急涨且无有效破位MA。"
            )

        return self._pass(detail, observation_meta=meta)
