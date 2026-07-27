"""Phase 2 引擎统一回归：回测与实盘共用同一成本/滑点/涨跌停来源。"""

from __future__ import annotations

from quant.backtest2.costs import CostModel
from quant.execution.sim_rules import TradeSimConfig, calc_buy_cost, calc_sell_proceeds, limit_pct, load_trade_sim_config


def test_limit_pct_board_and_st_aware():
    """sim_rules.limit_pct 现统一到 tradability 分档：主板10/创业板科创20/北交30/ST5。"""
    cfg = TradeSimConfig()
    assert limit_pct("600519", cfg) == 10.0
    assert limit_pct("300750", cfg) == 20.0
    assert limit_pct("688981", cfg) == 20.0
    assert limit_pct("830879", cfg) == 30.0  # 北交（旧实现漏判，误为 9.9 主板）
    assert limit_pct("000001", cfg, name="*ST 某股") == 5.0  # ST（旧实现漏判，误为 9.9）


def test_backtest_buy_cost_matches_live():
    """SimBroker(CostModel) 买入成本 == 实盘 calc_buy_cost（同一 sim 配置）。"""
    sim = load_trade_sim_config()
    cm = CostModel(sim=sim)
    price, shares, code = 12.34, 2000, "600000"
    bd = calc_buy_cost(price, shares, code, sim)
    # 滑点后成交价一致（须传同一 quantity：滑点 notional 项依赖股数）
    assert abs(cm.fill_price(price, True, code=code, quantity=shares) - bd.fill_price) < 1e-9
    # 成本（佣金+过户费）一致；滑点仅在 fill_price 计一次
    assert abs(cm.buy_cost(bd.fill_price, shares, code=code) - (bd.commission + bd.transfer_fee)) < 1e-6


def test_backtest_sell_cost_matches_live():
    """SimBroker(CostModel) 卖出成本 == 实盘 calc_sell_proceeds。"""
    sim = load_trade_sim_config()
    cm = CostModel(sim=sim)
    price, shares, code, buy_price = 21.0, 1500, "600000", 18.0
    sp = calc_sell_proceeds(price, shares, code, buy_price, sim)
    assert abs(cm.fill_price(price, False, code=code, quantity=shares) - sp.fill_price) < 1e-9
    assert abs(cm.sell_cost(sp.fill_price, shares, code=code) - (sp.commission + sp.stamp_tax + sp.transfer_fee)) < 1e-6


def test_shenzhen_no_transfer_fee_parity():
    """深市无过户费：回测与实盘一致（旧 CostModel 对深市误收过户费）。"""
    sim = load_trade_sim_config()
    cm = CostModel(sim=sim)
    bd = calc_buy_cost(10.0, 1000, "000001", sim)
    assert bd.transfer_fee == 0
    assert cm.buy_cost(bd.fill_price, 1000, code="000001") == bd.commission  # 仅佣金
