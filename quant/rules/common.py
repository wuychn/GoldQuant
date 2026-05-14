"""共用约束规则：标的池过滤、极端行情熔断、止损冷却期。"""

from __future__ import annotations

from typing import Any

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


class NoBuyGapUpWeakIntradayRule(Rule):
    """盘中买入前弱势过滤套件：多项子检查均可独立开关与调参（见 default_params）。

    子项全部为「未启用则跳过；启用则未通过则不买（FAIL）」；数据缺失时该子项不拦截（PASS）。
    典型用于规避高开低走、缺口过快回补、分时均价下方运行、长上影、全市场偏弱等场景。
    """

    def default_params(self) -> dict[str, Any]:
        return {
            # --- 最早允许看盘买入时刻（本地系统时间，与 LHT 买点时间规则一致习惯）---
            "enable_earliest_buy_clock": False,
            "earliest_buy_hour": 10,
            "earliest_buy_minute": 30,
            # --- 全市场赚钱效应：上涨家数 > 下跌家数（JSON「赚钱效应」）---
            "enable_market_advancers_gt_decliners": False,
            # --- 个股当日涨幅下限（盘口「涨幅」，单位 %）---
            "enable_min_change_pct": False,
            "min_change_pct": 0.0,
            # --- 最新价须在分时均价之上（比例 ≥1 表示不低于均价）---
            "enable_latest_above_avg": False,
            "latest_vs_avg_min_ratio": 1.0,
            # --- 主力净流入为正（个股资金流最后一条）---
            "enable_main_net_inflow_positive": False,
            # --- 日线 MA5≥MA10≥MA20 ---
            "enable_ma_bull_stack": False,
            # --- 上影线相对昨收过长（(最高-max(今开,最新))/昨收×100）---
            "enable_max_upper_shadow_pct": False,
            "max_upper_shadow_vs_prev_pct": 4.0,
            # --- 从日内高点回撤过大（(最高-最新)/最高×100）---
            "enable_min_pullback_from_high_pct": False,
            "min_pullback_from_high_pct": 3.0,
            "pullback_from_high_only_when_below_open": True,
            # --- 向上跳空缺口回补过多（(今开-最新)/(今开-昨收)×100，今开>昨收时）---
            "enable_max_gap_fill_pct": False,
            "max_gap_fill_pct": 65.0,
            # --- 量比下限（盘口「量比」）---
            "enable_min_volume_ratio": False,
            "min_volume_ratio": 1.0,
            # --- 核心：显著高开且最新已跌破今开（可关则本块不判 FAIL）---
            "enable_core_gap_below_open": True,
            "min_gap_up_pct": 0.8,
            "require_latest_below_open": True,
            "require_ma_touch_zone": False,
            "ma_low_ratio": 0.99,
            "ma_high_ratio": 1.02,
        }

    @property
    def name(self) -> str:
        return "回避高开低走弱势"

    @staticmethod
    def _to_float(v: object, default: float | None = None) -> float | None:
        if v is None or v == "":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        code = str(stock.get("股票代码", "")).strip()
        pankou = stock.get("盘口", {}) if isinstance(stock.get("盘口"), dict) else {}
        tech = stock.get("技术指标", {}) if isinstance(stock.get("技术指标"), dict) else {}

        today_open = pankou.get("今开")
        yesterday_close = pankou.get("昨收")
        latest = pankou.get("最新")

        if today_open is None or yesterday_close is None or latest is None:
            return self._skip(f"{name}({code})盘口缺今开/昨收/最新")

        open_f = self._to_float(today_open)
        prev_f = self._to_float(yesterday_close)
        latest_f = self._to_float(latest)
        if open_f is None or prev_f is None or latest_f is None:
            return self._skip(f"{name}({code})今开/昨收/最新格式异常")
        if prev_f <= 0 or open_f <= 0:
            return self._skip(f"{name}({code})昨收或今开无效")

        # ---- 子检查：最早买入时刻 ----
        if bool(self.params.get("enable_earliest_buy_clock", False)):
            from datetime import datetime

            eh = int(self.params.get("earliest_buy_hour", 10))
            em = int(self.params.get("earliest_buy_minute", 0))
            now = datetime.now()
            if now.hour < eh or (now.hour == eh and now.minute < em):
                return self._fail(
                    f"当前早于允许买入时刻（需≥{eh:02d}:{em:02d}）"
                )

        # ---- 赚钱效应 ----
        if bool(self.params.get("enable_market_advancers_gt_decliners", False)):
            pe = ctx.profit_effect if isinstance(ctx.profit_effect, dict) else {}
            up_raw = pe.get("上涨")
            down_raw = pe.get("下跌")
            try:
                up_n = int(float(up_raw)) if up_raw is not None else None
                down_n = int(float(down_raw)) if down_raw is not None else None
            except (TypeError, ValueError):
                up_n = down_n = None
            if up_n is not None and down_n is not None and up_n <= down_n:
                return self._fail(
                    f"全市场赚钱效应偏弱（涨{up_n}/跌{down_n}）"
                )

        # ---- 涨幅下限 ----
        if bool(self.params.get("enable_min_change_pct", False)):
            chg = self._to_float(pankou.get("涨幅"))
            min_chg = float(self.params.get("min_change_pct", 0.0))
            if chg is not None and chg < min_chg:
                return self._fail(
                    f"当日涨幅{chg:.2f}%<{min_chg:.2f}%，承接偏弱"
                )

        # ---- 分时均价 ----
        if bool(self.params.get("enable_latest_above_avg", False)):
            avg = self._to_float(pankou.get("均价"))
            ratio = float(self.params.get("latest_vs_avg_min_ratio", 1.0))
            if avg is not None and avg > 0 and latest_f < avg * ratio:
                return self._fail(
                    f"最新{latest_f:.2f}<分时均价×{ratio:.4f}={avg*ratio:.2f}，未站稳均价"
                )

        # ---- 主力净流入 ----
        if bool(self.params.get("enable_main_net_inflow_positive", False)):
            flows = stock.get("个股资金流", [])
            if isinstance(flows, list) and flows:
                last = flows[-1] if isinstance(flows[-1], dict) else {}
                net = last.get("主力净流入-净额", last.get("净额"))
                net_f = self._to_float(net)
                if net_f is not None and net_f <= 0:
                    return self._fail(
                        f"主力净流入{net_f:.0f}≤0，资金未做多"
                    )

        # ---- 均线多头 ----
        if bool(self.params.get("enable_ma_bull_stack", False)):
            m5 = self._to_float(tech.get("均线5日"))
            m10 = self._to_float(tech.get("均线10日"))
            m20 = self._to_float(tech.get("均线20日"))
            if (
                m5 is not None
                and m10 is not None
                and m20 is not None
                and m5 > 0
                and m10 > 0
                and m20 > 0
            ):
                if not (m5 >= m10 >= m20):
                    return self._fail(
                        f"均线未多头（MA5={m5:.2f}, MA10={m10:.2f}, MA20={m20:.2f}）"
                    )

        # ---- 上影线 ----
        if bool(self.params.get("enable_max_upper_shadow_pct", False)):
            high_f = self._to_float(pankou.get("最高"))
            max_sh = float(self.params.get("max_upper_shadow_vs_prev_pct", 4.0))
            if high_f is not None and high_f > 0:
                body_top = max(open_f, latest_f)
                shadow_pct = (high_f - body_top) / prev_f * 100.0
                if shadow_pct > max_sh:
                    return self._fail(
                        f"上影线占昨收{shadow_pct:.2f}%>{max_sh:.2f}%，抛压形态"
                    )

        # ---- 日内高点回撤 ----
        if bool(self.params.get("enable_min_pullback_from_high_pct", False)):
            high_f = self._to_float(pankou.get("最高"))
            min_pb = float(self.params.get("min_pullback_from_high_pct", 3.0))
            only_bo = bool(
                self.params.get("pullback_from_high_only_when_below_open", True)
            )
            if high_f is not None and high_f > 0:
                pb = (high_f - latest_f) / high_f * 100.0
                if (not only_bo or latest_f < open_f) and pb >= min_pb:
                    return self._fail(
                        f"自日内高点回撤{pb:.2f}%≥{min_pb:.2f}%，分时走弱"
                    )

        # ---- 缺口回补 ----
        if bool(self.params.get("enable_max_gap_fill_pct", False)):
            max_fill = float(self.params.get("max_gap_fill_pct", 65.0))
            if open_f > prev_f:
                gap = open_f - prev_f
                filled = open_f - latest_f
                fill_pct = filled / gap * 100.0 if gap > 0 else 0.0
                if fill_pct > max_fill:
                    return self._fail(
                        f"向上缺口已回补{fill_pct:.1f}%>{max_fill:.1f}%，动能不足"
                    )

        # ---- 量比 ----
        if bool(self.params.get("enable_min_volume_ratio", False)):
            min_vr = float(self.params.get("min_volume_ratio", 1.0))
            vr = self._to_float(pankou.get("量比"))
            if vr is not None and vr < min_vr:
                return self._fail(
                    f"量比{vr:.2f}<{min_vr:.2f}，量能不足"
                )

        # ---- 核心：显著高开 + 可选跌破今开 + 可选仅均线带内拦截 ----
        if bool(self.params.get("enable_core_gap_below_open", True)):
            gap_pct = (open_f - prev_f) / prev_f * 100.0
            min_gap = float(self.params.get("min_gap_up_pct", 0.8))
            if gap_pct >= min_gap:
                req_below = bool(self.params.get("require_latest_below_open", True))
                if req_below and latest_f >= open_f:
                    return self._pass(
                        f"高开{gap_pct:.2f}%但最新{latest_f:.2f}≥今开{open_f:.2f}，核心弱势不触发"
                    )
                if bool(self.params.get("require_ma_touch_zone", False)):
                    lo = float(self.params.get("ma_low_ratio", 0.99))
                    hi = float(self.params.get("ma_high_ratio", 1.02))
                    in_zone = False
                    for key in ("均线5日", "均线10日", "均线20日"):
                        ma_v = self._to_float(tech.get(key))
                        if ma_v is not None and ma_v > 0:
                            if ma_v * lo <= latest_f <= ma_v * hi:
                                in_zone = True
                                break
                    if not in_zone:
                        return self._pass("未贴近均线区间，核心过滤不触发")
                if req_below and latest_f < open_f:
                    return self._fail(
                        f"高开{gap_pct:.2f}%且最新{latest_f:.2f}<今开{open_f:.2f}，日内弱势，回避买入"
                    )

        return self._pass("弱势过滤套件通过（或未触发已启用的子项）")
