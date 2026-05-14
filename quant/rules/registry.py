"""规则注册表、链工厂、配置加载。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from quant.rules.base import Rule, RuleChain
from quant.rules.common import (
    ExtremeMarketCircuitBreakerRule,
    NoBuyGapUpWeakIntradayRule,
    StockPoolFilterRule,
    StopLossCoolingRule,
)
from quant.rules.context import RuleContext
from quant.rules.intraday_10m_buy import Intraday10mTrendGuardRule
from quant.rules.lht_buy import (
    LHTMainCapitalInflowRule,
    LHTPriceNearMARule,
    LHTTimeAfter10Rule,
    LHTVolumeRatioRule,
)
from quant.rules.lht_sell import (
    LHTAboveMA5Rule,
    LHTCapitalOutflowRule,
    LHTMABreakdownRule,
    LHTMAStopLossRule,
    LHTProfitTargetRule,
    LHTReboundPullbackRule,
    LHTTimeStopLossRule,
)
from quant.rules.lht_watchlist import (
    LHTConsecutiveUpRule,
    LHTMACDRule,
    LHTMASupportRule,
    LHTMaxConsecutiveZTRunRule,
    LHTPopularityRule,
    LHTPullbackRule,
    LHTVolumeRule,
)
from quant.rules.zsll_buy import (
    ZSLMainCapitalInflowRule,
    ZSLPriceRidingMARule,
    ZSLTimeAfter10Rule,
    ZSLVolumeRatioRule,
)
from quant.rules.zsll_sell import (
    ZSLAboveMA5Rule,
    ZSLCapitalOutflowRule,
    ZSLMABreakdownRule,
    ZSLMAStopLossRule,
    ZSLProfitTargetRule,
    ZSLReboundPullbackRule,
    ZSLTimeStopLossRule,
)
from quant.rules.zsll_watchlist import (
    ZSLMACDRule,
    ZSLMaLongTrendRule,
    ZSLPopularityRule,
    ZSLPositiveDaysRatioRule,
    ZSLTrendGainRule,
    ZSLVolumeThrustRule,
    ZSLWindowMaxDrawdownRule,
)
from quant.rules.market_state import (
    MarketStateDeterminationRule,
    SentimentDecayWarningRule,
)
from quant.rules.position import DailyLossLimitRule, PositionLimitRule
from quant.rules.zt_buy import (
    ZTAboveAvgPriceRule,
    ZTAuctionVolumeRule,
    ZTGainNotOverheatedRule,
    ZTHighOpenRule,
    ZTIntradayConceptRule,
    ZTIntradayPopularityRule,
    ZTNotOneBoardRule,
    ZTVolumeRatioPreRule,
)
from quant.rules.zt_sell import (
    ZTAboveAvgHoldRule,
    ZTATRStopLossRule,
    ZTOpenPatternSellRule,
    ZTProfitPullbackRule,
    ZTTimeStopLossRule,
    ZTWeaknessOutflowRule,
)
from quant.rules.zt_watchlist import (
    ZTConceptResonanceRule,
    ZTConsecutiveBoardsRule,
    ZTMarketCapRule,
    ZTPopularityRule,
)

# 配置文件路径：优先项目目录下的 quant/rules_config.yml
_CONFIG_FILE = Path(__file__).resolve().parent.parent / "rules_config.yml"


def _load_config() -> dict[str, dict[str, Any]]:
    """加载 YAML 规则配置（启用/禁用 + 参数）。"""
    if not _CONFIG_FILE.is_file():
        return {}
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except (yaml.YAMLError, OSError):
        return {}


def _apply_config(chain_key: str, rules: list[Rule], config: dict) -> None:
    """根据配置文件设置规则的 enabled 状态和参数。

    YAML 中的规则键可以是类名（如 ZTPopularity）或规则名（如 ZT人气排名）。
    匹配规则: 类名去掉末尾 'Rule' 后缀 == YAML 中的键。

    对于 common/position 中定义的规则，会作为 fallback 应用到所有链中。
    """
    # 合并查找顺序: 链专属配置 > common > position
    chain_config = config.get(chain_key, {})
    fallback_sections = ["common", "position"]
    merged: dict[str, Any] = {}
    for fb in fallback_sections:
        fb_cfg = config.get(fb, {})
        if isinstance(fb_cfg, dict):
            merged.update(fb_cfg)
    if isinstance(chain_config, dict):
        merged.update(chain_config)  # 链专属覆盖 fallback

    if not merged:
        return

    # 建立查找映射：类名(去Rule后缀) -> 规则实例
    class_map: dict[str, Rule] = {}
    name_map: dict[str, Rule] = {}
    for rule in rules:
        cls_key = rule.__class__.__name__
        if cls_key.endswith("Rule"):
            cls_key = cls_key[:-4]
        class_map[cls_key] = rule
        name_map[rule.name] = rule

    for cfg_key, rule_cfg in merged.items():
        rule = class_map.get(cfg_key) or name_map.get(cfg_key)
        if rule is None:
            continue
        if isinstance(rule_cfg, dict):
            if "enabled" in rule_cfg:
                rule.enabled = bool(rule_cfg["enabled"])
            params = rule_cfg.get("params")
            if isinstance(params, dict) and hasattr(rule, "params"):
                rule.params.update(params)
        elif isinstance(rule_cfg, bool):
            rule.enabled = rule_cfg


# ---------------------------------------------------------------------------
# 链工厂
# ---------------------------------------------------------------------------

def build_global_preconditions() -> RuleChain:
    """全局前置条件链：市场状态 + 情绪退潮 + 极端熔断 + 亏损限额。"""
    config = _load_config()
    rules: list[Rule] = [
        MarketStateDeterminationRule(),
        SentimentDecayWarningRule(),
        ExtremeMarketCircuitBreakerRule(),
        DailyLossLimitRule(),
    ]
    _apply_config("global_preconditions", rules, config)
    return RuleChain("全局前置条件", rules, halt_on_first_failure=False)


def build_zt_watchlist_chain() -> RuleChain:
    """涨停板战法-加自选规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        StockPoolFilterRule(),
        SentimentDecayWarningRule(),
        ZTPopularityRule(),
        ZTMarketCapRule(),
        ZTConsecutiveBoardsRule(),
        ZTConceptResonanceRule(),
    ]
    _apply_config("zt_watchlist", rules, config)
    return RuleChain("涨停板-加自选", rules, halt_on_first_failure=True)


def build_zt_buy_pre_chain() -> RuleChain:
    """涨停板战法-盘前买入规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        StockPoolFilterRule(),
        SentimentDecayWarningRule(),
        StopLossCoolingRule(),
        ZTHighOpenRule(),
        ZTVolumeRatioPreRule(),
        ZTNotOneBoardRule(),
        ZTAuctionVolumeRule(),
    ]
    _apply_config("zt_buy_pre", rules, config)
    return RuleChain("涨停板-盘前买入", rules, halt_on_first_failure=True)


def build_zt_buy_intraday_chain() -> RuleChain:
    """涨停板战法-盘中买入规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        StockPoolFilterRule(),
        SentimentDecayWarningRule(),
        StopLossCoolingRule(),
        PositionLimitRule(),
        NoBuyGapUpWeakIntradayRule(),
        Intraday10mTrendGuardRule(),
        ZTIntradayPopularityRule(),
        ZTIntradayConceptRule(),
        ZTAboveAvgPriceRule(),
        ZTGainNotOverheatedRule(),
    ]
    _apply_config("zt_buy_intraday", rules, config)
    return RuleChain("涨停板-盘中买入", rules, halt_on_first_failure=True)


def build_zt_hold_chain() -> RuleChain:
    """涨停板战法-持股监控规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        ZTAboveAvgHoldRule(),
        ZTWeaknessOutflowRule(),
        ZTProfitPullbackRule(),
    ]
    _apply_config("zt_hold", rules, config)
    return RuleChain("涨停板-持股监控", rules, halt_on_first_failure=False)


def build_zt_sell_chain() -> RuleChain:
    """涨停板战法-卖出规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        ZTOpenPatternSellRule(),
        ZTATRStopLossRule(),
        ZTTimeStopLossRule(),
    ]
    _apply_config("zt_sell", rules, config)
    return RuleChain("涨停板-卖出", rules, halt_on_first_failure=False)


def build_lht_watchlist_chain() -> RuleChain:
    """龙回头战法-加自选规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        StockPoolFilterRule(),
        LHTPopularityRule(),
        LHTMaxConsecutiveZTRunRule(),
        LHTPullbackRule(),
        LHTMASupportRule(),
        LHTConsecutiveUpRule(),
        LHTVolumeRule(),
        LHTMACDRule(),
    ]
    _apply_config("lht_watchlist", rules, config)
    return RuleChain("龙回头-加自选", rules, halt_on_first_failure=True)


def build_lht_buy_chain() -> RuleChain:
    """龙回头战法-买入规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        StockPoolFilterRule(),
        StopLossCoolingRule(),
        PositionLimitRule(),
        NoBuyGapUpWeakIntradayRule(),
        Intraday10mTrendGuardRule(),
        LHTPriceNearMARule(),
        LHTTimeAfter10Rule(),
        LHTVolumeRatioRule(),
        LHTMainCapitalInflowRule(),
    ]
    _apply_config("lht_buy", rules, config)
    return RuleChain("龙回头-买入", rules, halt_on_first_failure=True)


def build_lht_hold_chain() -> RuleChain:
    """龙回头战法-持股监控规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        LHTAboveMA5Rule(),
        LHTMABreakdownRule(),
        LHTCapitalOutflowRule(),
        LHTReboundPullbackRule(),
    ]
    _apply_config("lht_hold", rules, config)
    return RuleChain("龙回头-持股监控", rules, halt_on_first_failure=False)


def build_lht_sell_chain() -> RuleChain:
    """龙回头战法-卖出规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        LHTProfitTargetRule(),
        LHTMAStopLossRule(),
        LHTTimeStopLossRule(),
    ]
    _apply_config("lht_sell", rules, config)
    return RuleChain("龙回头-卖出", rules, halt_on_first_failure=False)


def build_zsll_watchlist_chain() -> RuleChain:
    """主升浪战法-加自选规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        StockPoolFilterRule(),
        ZSLPopularityRule(),
        ZSLTrendGainRule(),
        ZSLMaLongTrendRule(),
        ZSLWindowMaxDrawdownRule(),
        ZSLPositiveDaysRatioRule(),
        ZSLVolumeThrustRule(),
        ZSLMACDRule(),
    ]
    _apply_config("zsll_watchlist", rules, config)
    return RuleChain("主升浪-加自选", rules, halt_on_first_failure=True)


def build_zsll_buy_chain() -> RuleChain:
    """主升浪战法-买入规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        StockPoolFilterRule(),
        StopLossCoolingRule(),
        PositionLimitRule(),
        NoBuyGapUpWeakIntradayRule(),
        Intraday10mTrendGuardRule(),
        ZSLPriceRidingMARule(),
        ZSLTimeAfter10Rule(),
        ZSLVolumeRatioRule(),
        ZSLMainCapitalInflowRule(),
    ]
    _apply_config("zsll_buy", rules, config)
    return RuleChain("主升浪-买入", rules, halt_on_first_failure=True)


def build_zsll_hold_chain() -> RuleChain:
    """主升浪战法-持股监控规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        ZSLAboveMA5Rule(),
        ZSLMABreakdownRule(),
        ZSLCapitalOutflowRule(),
        ZSLReboundPullbackRule(),
    ]
    _apply_config("zsll_hold", rules, config)
    return RuleChain("主升浪-持股监控", rules, halt_on_first_failure=False)


def build_zsll_sell_chain() -> RuleChain:
    """主升浪战法-卖出规则链。"""
    config = _load_config()
    rules: list[Rule] = [
        ZSLProfitTargetRule(),
        ZSLMAStopLossRule(),
        ZSLTimeStopLossRule(),
    ]
    _apply_config("zsll_sell", rules, config)
    return RuleChain("主升浪-卖出", rules, halt_on_first_failure=False)


# ---------------------------------------------------------------------------
# 便捷方法：根据模式获取所有相关链
# ---------------------------------------------------------------------------

def get_chains_for_mode(mode: str) -> dict[str, RuleChain]:
    """根据运行模式返回相关规则链字典。"""
    chains: dict[str, RuleChain] = {"global": build_global_preconditions()}

    if mode == "pre_market":
        chains["zt_buy_pre"] = build_zt_buy_pre_chain()
        chains["lht_buy"] = build_lht_buy_chain()
        chains["zsll_buy"] = build_zsll_buy_chain()
        chains["zt_hold"] = build_zt_hold_chain()
        chains["lht_hold"] = build_lht_hold_chain()
        chains["zsll_hold"] = build_zsll_hold_chain()
    elif mode == "during_market":
        chains["zt_buy_intraday"] = build_zt_buy_intraday_chain()
        chains["lht_buy"] = build_lht_buy_chain()
        chains["zsll_buy"] = build_zsll_buy_chain()
        chains["zt_hold"] = build_zt_hold_chain()
        chains["zt_sell"] = build_zt_sell_chain()
        chains["lht_hold"] = build_lht_hold_chain()
        chains["lht_sell"] = build_lht_sell_chain()
        chains["zsll_hold"] = build_zsll_hold_chain()
        chains["zsll_sell"] = build_zsll_sell_chain()
    elif mode in ("post_market_lunch", "post_market_evening"):
        chains["zt_watchlist"] = build_zt_watchlist_chain()
        chains["lht_watchlist"] = build_lht_watchlist_chain()
        chains["zsll_watchlist"] = build_zsll_watchlist_chain()

    return chains


def run_global_check(ctx: RuleContext) -> str:
    """运行全局前置检查，返回摘要文本。"""
    chain = build_global_preconditions()
    result = chain.evaluate(ctx)
    return result.summary()


def run_stock_chain(chain: RuleChain, ctx: RuleContext, stock: dict[str, Any]) -> str:
    """对单只股票运行规则链，返回摘要。"""
    ctx.target_stock = stock
    result = chain.evaluate(ctx)
    return result.summary()
