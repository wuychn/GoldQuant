"""100 分制评分引擎：多维度加权 + 战法标签。

流程
----
1. 读取 scoring.yml（含 ML 覆盖）
2. 对每只 stock 调用各 enabled 维度的 Scorer（0~100 分）
3. 总分 = Σ(得分×权重) / Σ(权重)，仅统计 available 的维度
4. classify_strategy() 打战法标签（影响 gates 单票仓位上限）

阈值
----
watchlist_threshold / buy_threshold / sell_threshold 由 apply_threshold() 或
signals 模块直接读取 config 使用。
"""

from __future__ import annotations

from quant.config import load_scoring_config
from quant.scoring.context import ScoreContext, find_in_zt_pool
from quant.scoring.dimensions.concept_theme import ConceptThemeScorer
from quant.scoring.dimensions.market_fund_flow import MarketFundFlowScorer
from quant.scoring.dimensions.market_index import MarketIndexScorer
from quant.scoring.dimensions.market_sentiment import MarketSentimentScorer
from quant.scoring.dimensions.popularity import PopularityRankScorer
from quant.scoring.dimensions.stock_fund_flow import StockFundFlowScorer
from quant.scoring.dimensions.stock_history import StockHistoryScorer
from quant.scoring.dimensions.stub import StubScorer
from quant.scoring.dimensions.technical import TechnicalScorer
from quant.scoring.dimensions.zt_stats import ZtCountScorer, ZtHeightScorer
from quant.scoring.models import DimensionResult, StockScore

# 维度 key 须与 scoring.yml 中 dimensions 键名一致
_SCORERS = {
    "market_index": MarketIndexScorer(),
    "market_sentiment": MarketSentimentScorer(),
    "market_fund_flow": MarketFundFlowScorer(),
    "zt_count": ZtCountScorer(),
    "zt_height": ZtHeightScorer(),
    "concept_theme": ConceptThemeScorer(),
    "stock_history": StockHistoryScorer(),
    "stock_fund_flow": StockFundFlowScorer(),
    "popularity_rank": PopularityRankScorer(),
    "technical": TechnicalScorer(),
    "minute_bars": StubScorer("minute_bars"),
    "us_overnight": StubScorer("us_overnight"),
    "stock_news": StubScorer("stock_news"),
}


def classify_strategy(stock: dict, ctx: ScoreContext) -> str:
    """根据连板/回撤/涨幅等结构打战法标签，用于仓位 single_pct 查表。"""
    code = str(stock.get("股票代码", "")).strip()
    zt = find_in_zt_pool(code, ctx.payload)
    boards = 0
    if zt:
        try:
            boards = int(float(zt.get("连板数", 0) or 0))
        except (TypeError, ValueError):
            boards = 0
    if boards >= 2:
        return "涨停板战法"
    hist = stock.get("历史行情") or []
    if isinstance(hist, list) and len(hist) >= 10:
        try:
            highs = [float(r.get("最高", 0) or 0) for r in hist[-20:] if isinstance(r, dict)]
            closes = [float(r.get("收盘", 0) or 0) for r in hist[-20:] if isinstance(r, dict)]
            if highs and closes:
                peak = max(highs)
                last = closes[-1]
                if peak > 0 and (peak - last) / peak >= 0.15 and boards >= 1:
                    return "龙回头战法"
                gain = (last - closes[0]) / closes[0] * 100 if closes[0] else 0
                if gain >= 15:
                    return "主升浪战法"
        except (TypeError, ValueError):
            pass
    return "趋势"


class ScoringEngine:
    """评分引擎入口。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_scoring_config()
        self.dimensions_cfg = self.config.get("dimensions") or {}

    def score_stock(self, ctx: ScoreContext, stock: dict) -> StockScore:
        """计算单只股票总分与各维度明细。"""
        code = str(stock.get("股票代码", "")).strip()
        name = str(stock.get("股票名称", "")).strip()
        results: list[DimensionResult] = []
        weighted = 0.0
        weight_sum = 0.0
        for key, scorer in _SCORERS.items():
            dim_cfg = self.dimensions_cfg.get(key) or {}
            enabled = bool(dim_cfg.get("enabled", False))
            weight = float(dim_cfg.get("weight", 0) or 0)
            if not enabled or weight <= 0:
                continue
            dr = scorer.score(ctx, stock)
            dr.weight = weight
            dr.enabled = True
            # 数据缺失的维度不参与加权（weight_sum 不增加）
            if dr.available:
                weighted += dr.score * weight
                weight_sum += weight
            results.append(dr)
        total = weighted / weight_sum if weight_sum else 0.0
        strategy = classify_strategy(stock, ctx)
        return StockScore(
            code=code,
            name=name,
            total=total,
            strategy=strategy,
            dimensions=results,
        )

    def score_many(self, ctx: ScoreContext, stocks: list[dict]) -> list[StockScore]:
        return [self.score_stock(ctx, s) for s in stocks if str(s.get("股票代码", "")).strip()]

    def apply_threshold(self, scores: list[StockScore], *, kind: str = "watchlist") -> list[StockScore]:
        """kind: watchlist | buy | sell — 对应 scoring.yml 中 *_threshold。"""
        key = f"{kind}_threshold"
        threshold = float(self.config.get(key, 65))
        out: list[StockScore] = []
        for s in scores:
            s.passed_threshold = s.total >= threshold
            out.append(s)
        return out
