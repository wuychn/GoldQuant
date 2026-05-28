"""100 分制评分引擎 — 仅主升浪战法。"""

from __future__ import annotations

from quant.config import load_scoring_config
from quant.constants import STRATEGY_NAME
from quant.scoring.context import ScoreContext
from quant.scoring.dimensions.concept_theme import ConceptThemeScorer
from quant.scoring.dimensions.main_wave import MainWaveScorer
from quant.scoring.dimensions.market_fund_flow import MarketFundFlowScorer
from quant.scoring.dimensions.market_index import MarketIndexScorer
from quant.scoring.dimensions.market_sentiment import MarketSentimentScorer
from quant.scoring.dimensions.pkyd_signal import PkydSignalScorer
from quant.scoring.dimensions.popularity import PopularityRankScorer
from quant.scoring.dimensions.stock_fund_flow import StockFundFlowScorer
from quant.scoring.dimensions.stock_history import StockHistoryScorer
from quant.scoring.dimensions.stub import StubScorer
from quant.scoring.dimensions.technical import TechnicalScorer
from quant.scoring.dimensions.zt_stats import ZtCountScorer, ZtHeightScorer
from quant.scoring.models import DimensionResult, StockScore

_SCORERS = {
    "main_wave": MainWaveScorer(),
    "market_index": MarketIndexScorer(),
    "market_sentiment": MarketSentimentScorer(),
    "market_fund_flow": MarketFundFlowScorer(),
    "zt_count": ZtCountScorer(),
    "zt_height": ZtHeightScorer(),
    "concept_theme": ConceptThemeScorer(),
    "stock_history": StockHistoryScorer(),
    "stock_fund_flow": StockFundFlowScorer(),
    "popularity_rank": PopularityRankScorer(),
    "pkyd_signal": PkydSignalScorer(),
    "technical": TechnicalScorer(),
    "minute_bars": StubScorer("minute_bars"),
    "us_overnight": StubScorer("us_overnight"),
    "stock_news": StubScorer("stock_news"),
}


class ScoringEngine:
    def __init__(self, config: dict | None = None):
        self.config = config or load_scoring_config()
        self.dimensions_cfg = self.config.get("dimensions") or {}

    def score_stock(self, ctx: ScoreContext, stock: dict) -> StockScore:
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
            if dr.available:
                weighted += dr.score * weight
                weight_sum += weight
            results.append(dr)
        total = weighted / weight_sum if weight_sum else 0.0
        return StockScore(
            code=code,
            name=name,
            total=total,
            strategy=STRATEGY_NAME,
            dimensions=results,
        )

    def main_wave_score(self, score: StockScore) -> float | None:
        for d in score.dimensions:
            if d.name == "main_wave" and d.available:
                return d.score
        return None

    def passes_main_wave(self, score: StockScore) -> bool:
        """加自选/买入须满足主升浪维度可用且不低于 watchlist 阈值的一半（可配）。"""
        mw = self.main_wave_score(score)
        if mw is None:
            return False
        floor = float(self.config.get("watchlist_threshold", 65)) * 0.55
        return mw >= floor

    def score_many(self, ctx: ScoreContext, stocks: list[dict]) -> list[StockScore]:
        return [self.score_stock(ctx, s) for s in stocks if str(s.get("股票代码", "")).strip()]

    def apply_threshold(
        self,
        scores: list[StockScore],
        *,
        kind: str = "watchlist",
        stock_rows: dict[str, dict] | None = None,
    ) -> list[StockScore]:
        key = f"{kind}_threshold"
        threshold = float(self.config.get(key, 65))
        cand_cfg = self.config.get("candidate") or {}
        require_mw = bool(cand_cfg.get("require_main_wave", True))
        require_mw_pkyd = bool(cand_cfg.get("require_main_wave_pkyd", False))
        rows = stock_rows or {}
        out: list[StockScore] = []
        for s in scores:
            ok = s.total >= threshold
            if require_mw and kind == "watchlist":
                row = rows.get(s.code, {})
                from_pkyd = str(row.get("候选来源") or "").strip() == "盘口异动"
                if from_pkyd and not require_mw_pkyd:
                    pass
                else:
                    ok = ok and self.passes_main_wave(s)
            s.passed_threshold = ok
            out.append(s)
        return out
