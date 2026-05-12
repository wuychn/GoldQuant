"""涨停板战法-买入规则（盘前4条 + 盘中4条）。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


# ---------------------------------------------------------------------------
# 集合竞价阶段（盘前）
# ---------------------------------------------------------------------------

class ZTHighOpenRule(Rule):
    """盘前条件1：高开1%~7%。"""

    def __init__(self, *, min_open: float = 1.0, max_open: float = 7.0, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.min_open = min_open
        self.max_open = max_open

    @property
    def name(self) -> str:
        return "ZT高开幅度"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        code = str(stock.get("股票代码", "")).strip()
        pankou = stock.get("盘口", {})

        today_open = pankou.get("今开")
        yesterday_close = pankou.get("昨收")

        if today_open is None or yesterday_close is None:
            return self._skip(f"{name}({code})盘口数据缺失（今开/昨收）")

        try:
            open_f = float(today_open)
            close_f = float(yesterday_close)
        except (TypeError, ValueError):
            return self._skip(f"{name}({code})盘口数据格式异常")

        if close_f == 0:
            return self._skip("昨收为0")

        gap_pct = (open_f - close_f) / close_f * 100

        if self.min_open <= gap_pct <= self.max_open:
            return self._pass(f"高开{gap_pct:.2f}%，在[{self.min_open}%,{self.max_open}%]区间")
        return self._fail(f"高开{gap_pct:.2f}%，不在[{self.min_open}%,{self.max_open}%]区间")


class ZTVolumeRatioPreRule(Rule):
    """盘前条件2：量比≥1.2。"""

    def __init__(self, *, min_ratio: float = 1.2, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.min_ratio = min_ratio

    @property
    def name(self) -> str:
        return "ZT盘前量比"

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
            return self._skip(f"{name}量比数据格式异常: {ratio}")

        if ratio_f >= self.min_ratio:
            return self._pass(f"量比={ratio_f:.2f}≥{self.min_ratio}")
        return self._fail(f"量比={ratio_f:.2f}<{self.min_ratio}，不满足")


class ZTNotOneBoardRule(Rule):
    """盘前条件3：非一字板（今开≠涨停价）。"""

    @property
    def name(self) -> str:
        return "ZT非一字板"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})

        today_open = pankou.get("今开")
        limit_up = pankou.get("涨停")

        if today_open is None or limit_up is None:
            return self._skip(f"{name}盘口数据缺失（今开/涨停）")

        try:
            open_f = float(today_open)
            limit_f = float(limit_up)
        except (TypeError, ValueError):
            return self._skip(f"{name}数据格式异常")

        if abs(open_f - limit_f) < 0.001:
            return self._fail(f"一字涨停开盘（今开{open_f}=涨停价{limit_f}），不可竞价买入")
        return self._pass(f"非一字板（今开{open_f}≠涨停{limit_f}）")


class ZTAuctionVolumeRule(Rule):
    """盘前条件4：竞价有成交（集合竞价分钟行情最后一条成交量>0）。"""

    @property
    def name(self) -> str:
        return "ZT竞价成交"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()

        auction = stock.get("集合竞价分钟行情", [])
        if not auction:
            return self._skip(f"{name}无集合竞价分钟行情数据")

        last_bar = auction[-1] if isinstance(auction, list) else None
        if not isinstance(last_bar, dict):
            return self._skip(f"{name}集合竞价数据格式异常")

        vol = last_bar.get("成交量", last_bar.get("volume", 0))
        try:
            vol_f = float(vol)
        except (TypeError, ValueError):
            vol_f = 0

        if vol_f > 0:
            return self._pass(f"竞价最后一分钟成交量={vol_f:.0f}>0")
        return self._fail(f"竞价最后一分钟成交量={vol_f:.0f}，无成交")


# ---------------------------------------------------------------------------
# 盘中追击
# ---------------------------------------------------------------------------

class ZTIntradayPopularityRule(Rule):
    """盘中条件1：人气排名≤10。"""

    def __init__(self, *, max_rank: int = 10, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.max_rank = max_rank

    @property
    def name(self) -> str:
        return "ZT盘中人气排名"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()

        pop = ctx.find_in_popularity(code)
        if not pop:
            return self._fail(f"{name}({code})不在人气榜中")

        rank = pop.get("人气排名")
        if rank is None:
            return self._fail(f"{name}({code})人气榜中无排名数据")

        try:
            rank = int(rank)
        except (TypeError, ValueError):
            return self._fail(f"{name}({code})排名数据异常: {rank}")

        if rank <= self.max_rank:
            return self._pass(f"盘中人气排名={rank}≤{self.max_rank}")
        return self._fail(f"盘中人气排名={rank}>{self.max_rank}")


class ZTIntradayConceptRule(Rule):
    """盘中条件2：概念共振。"""

    @property
    def name(self) -> str:
        return "ZT盘中概念共振"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()

        pop = ctx.find_in_popularity(code)
        concepts = None
        if pop:
            concepts = pop.get("所属概念")
        if not concepts:
            concepts = stock.get("所属概念")

        if not concepts:
            return self._fail(f"{name}({code})无所属概念数据")

        if isinstance(concepts, str):
            concepts = [c.strip() for c in concepts.split(",") if c.strip()]

        hot = ctx.get_concept_top_industries()
        if not hot:
            return self._skip("概念板块数据缺失")

        matched = [c for c in concepts if c in hot]
        if matched:
            return self._pass(f"盘中概念共振：{','.join(matched)}")
        return self._fail(f"所属概念未匹配板块热点")


class ZTAboveAvgPriceRule(Rule):
    """盘中条件3：分时强势（最新价>均价）。"""

    @property
    def name(self) -> str:
        return "ZT分时强势"

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

        if avg_f == 0:
            return self._skip("均价为0")

        if latest_f > avg_f:
            return self._pass(f"最新{latest_f:.2f}>均价{avg_f:.2f}，分时均线上方运行")
        return self._fail(f"最新{latest_f:.2f}≤均价{avg_f:.2f}，分时偏弱")


class ZTGainNotOverheatedRule(Rule):
    """盘中条件4：涨幅未过热（<7%）。"""

    def __init__(self, *, max_gain: float = 7.0, enabled: bool = True):
        super().__init__(enabled=enabled)
        self.max_gain = max_gain

    @property
    def name(self) -> str:
        return "ZT涨幅未过热"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        name = str(stock.get("股票名称", "")).strip()
        pankou = stock.get("盘口", {})

        gain = pankou.get("涨幅")
        if gain is None:
            return self._skip(f"{name}盘口无涨幅数据")

        try:
            gain_f = float(gain)
        except (TypeError, ValueError):
            return self._skip(f"{name}涨幅数据格式异常: {gain}")

        if gain_f < self.max_gain:
            return self._pass(f"当前涨幅{gain_f:.2f}%<{self.max_gain}%，未过热")
        return self._fail(f"当前涨幅{gain_f:.2f}%≥{self.max_gain}%，追高风险大")
