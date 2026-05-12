"""涨停板战法-加自选规则（4条）。"""

from __future__ import annotations

from quant.rules.base import Rule, RuleResult
from quant.rules.context import RuleContext


class ZTPopularityRule(Rule):
    """涨停板加自选条件1：人气排名≤阈值。"""

    def default_params(self):
        return {"max_rank": 50}

    @property
    def name(self) -> str:
        return "ZT人气排名"

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
        return self._fail(f"人气排名={rank}>{max_rank}，不符合")


class ZTMarketCapRule(Rule):
    """涨停板加自选条件2：流通市值在范围内。"""

    def default_params(self):
        return {"min_cap_yi": 50, "max_cap_yi": 1000}

    @property
    def name(self) -> str:
        return "ZT流通市值"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        min_cap = float(self.params["min_cap_yi"])
        max_cap = float(self.params["max_cap_yi"])

        zt_item = ctx.find_in_limit_up(code)
        if zt_item is None:
            return self._fail(f"{name}({code})不在涨停统计中，无法获取流通市值")

        cap = zt_item.get("流通市值")
        if cap is None:
            return self._fail(f"{name}({code})涨停统计中无流通市值字段")

        try:
            cap_float = float(cap)
        except (TypeError, ValueError):
            return self._fail(f"{name}({code})流通市值数据异常: {cap}")

        # 单位判断：如果>10000认为单位是元，转为亿
        cap_yi = cap_float
        if cap_float > 10000:
            cap_yi = cap_float / 1_0000_0000

        if cap_yi < min_cap:
            return self._fail(f"流通市值={cap_yi:.1f}亿<{min_cap}亿，偏小")
        if cap_yi > max_cap:
            return self._fail(f"流通市值={cap_yi:.1f}亿>{max_cap}亿，超标")
        return self._pass(f"流通市值={cap_yi:.1f}亿，在[{min_cap},{max_cap}]范围内")


class ZTConsecutiveBoardsRule(Rule):
    """涨停板加自选条件3：连板数在范围内。"""

    def default_params(self):
        return {"min_boards": 2, "max_boards": 7}

    @property
    def name(self) -> str:
        return "ZT非首板"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        min_boards = int(self.params["min_boards"])
        max_boards = int(self.params["max_boards"])

        zt_item = ctx.find_in_limit_up(code)
        if zt_item is None:
            return self._fail(f"{name}({code})不在涨停统计中（今日未涨停），视为首板不合格")

        boards = zt_item.get("连板数")
        if boards is None:
            return self._fail(f"{name}({code})涨停统计中无连板数字段")

        try:
            boards = int(boards)
        except (TypeError, ValueError):
            return self._fail(f"{name}({code})连板数数据异常: {boards}")

        if boards < min_boards:
            return self._fail(f"连板数={boards}<{min_boards}，为首板不合格")
        if boards > max_boards:
            return self._fail(f"连板数={boards}>{max_boards}，高位风险过大")
        return self._pass(f"连板数={boards}，在[{min_boards},{max_boards}]范围内")


class ZTConceptResonanceRule(Rule):
    """涨停板加自选条件4：概念共振（所属概念匹配涨幅榜/资金流入榜前十）。"""

    def default_params(self):
        return {"min_resonance_stocks": 3}

    @property
    def name(self) -> str:
        return "ZT概念共振"

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        stock = ctx.target_stock
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()

        concepts = stock.get("所属概念")
        if concepts is None:
            pop = ctx.find_in_popularity(code)
            if pop:
                concepts = pop.get("所属概念")

        if not concepts:
            return self._fail(f"{name}({code})无所属概念数据")

        if isinstance(concepts, str):
            concepts = [c.strip() for c in concepts.split(",") if c.strip()]

        hot_industries = ctx.get_concept_top_industries()
        if not hot_industries:
            return self._skip("概念板块数据缺失，无法判定共振")

        matched = [c for c in concepts if c in hot_industries]
        if matched:
            return self._pass(f"概念共振：{','.join(matched)}命中板块热点")
        return self._fail(
            f"所属概念{concepts}未匹配涨幅榜/资金流入榜前十({list(hot_industries)[:5]}…)"
        )
