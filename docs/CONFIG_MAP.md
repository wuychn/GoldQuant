# quant.yml 配置键 → 读取代码映射

> 维护目的：让「文档系统 = 运行系统」。标注 **存活** / **僵尸（r1 残留，r3 未读）**。
> 最后对账：V8（2026-07-29）

## gates.trading

| 键 | 状态 | 读取位置 |
|---|---|---|
| `time_validation_enabled` | 存活 | `quant/config.trading_time_checks_enabled` → `executor` |
| `simulation.*` | 存活 | `quant/execution/sim_rules.TradeSimConfig`、回测 `costs.py` |

## gates.symbol_pool

| 键 | 状态 | 读取位置 |
|---|---|---|
| `prefixes` | 存活 | `app/utils/common_util.is_allowed_symbol_pool_code` |
| `exclude_st` | 存活 | `quant/pool/symbol_filter.apply_symbol_pool_filter` |
| `min_listing_days` | **僵尸** | yml=60，但 `symbol_filter` 仅过滤前缀/ST；universe 用 `schema.DEFAULT_MIN_LIST_DAYS=120` |

## gates.position

| 键 | 状态 | 说明 |
|---|---|---|
| `强势/震荡/弱势.total_pct` | **僵尸** | r1 regime 仓位；r3 `TargetPortfolio` 走 `target_vol` + MVO |
| `*.max_stocks` | **僵尸** | 同上 |

## gates.buy / gates.sell

| 键 | 状态 | 读取位置 |
|---|---|---|
| `buy.during_market.intraday.*` | **僵尸** | r1 盘中买入门禁；r3 盘中买入 = `ops/modes._intraday_buy_block` → `compose_intraday_alpha` |
| `buy.pre_market.max_change_pct` | **僵尸** | r3 未读 |
| `sell.late_session_after` | 存活 | `quant/trading_hours.py` |
| `sell.time_stop.*` | **僵尸** | r3 出场走 `exit/rules.py`（ATR/MA20/时间止损） |

## gates.circuit_breaker / daily_loss / stoploss

| 键 | 状态 | 读取位置 |
|---|---|---|
| `circuit_breaker.index_drop_pct` | 存活 | `quant/execution/risk_gate.py` |
| `daily_loss_limit_pct` | 存活 | `risk_gate.py` |
| `stoploss_cooldown_days` | 存活 | `risk_gate.py` |
| `block_same_day_rebuy_after_sell` | 存活 | `risk_gate.py` |

## portfolio

| 键 | 状态 | 读取位置 |
|---|---|---|
| `optimizer` | 存活 | `TargetPortfolio.from_config` |
| `optimizer_mvo.risk_aversion` | 存活 | `TargetPortfolio.from_config` |
| `covariance.method` | 存活 | `TargetPortfolio.target_weights`（ewma/shrink） |
| `risk_budget.vol_lookback` | 存活 | `TargetPortfolio.from_config` → 波动/协方差窗口 |
| `constraints.max_*_pct` | 存活 | `TargetPortfolio.from_config` → sector/concept cap |
| `style_exposure.alpha_weighted` | 存活（条件） | 仅 `optimizer!=mvo` 或协方差缺失时生效 |
| `style_exposure.max_small_cap_pct` | 存活 | 风格暴露约束 |
| `style_exposure.max_high_mom_pct` | 存活 | 风格暴露约束 |
| `continuous_position.*` | **僵尸** | yml 有 min/max pct，但 `TargetPortfolio` 无对应字段 |
| `risk.max_drawdown_*` | 存活 | `risk_gate.py` |

## research.factors

| 键 | 状态 | 读取位置 |
|---|---|---|
| `strict_oos_weights` | 存活 | `quant/config.load_factor_weights_info` |
| `ic_horizons` | 存活 | `scripts/factors/fit_weights` |
| `fdr_alpha` | 存活 | `quant/factors/weights.full_weight_map` |

## 权重文件

| 文件 | 状态 | 说明 |
|---|---|---|
| `config/factor_weights_ts.yml` | 存活（优先） | walk-forward OOS 时变权重 |
| `config/factor_weights.yml` | 存活 | 静态 IC 权重；`strict_oos=true` 且有 `as_of` 时不用于 live |
| `REGISTRY.weights()` | 回退 | 无上述文件时 live 用手调默认权重（R23） |

## 盘中 vs 日频路径（R25，文档备忘）

| 路径 | 入口 | 说明 |
|---|---|---|
| 日频回测 | `scripts/backtest/run.py` | T 开盘 alpha → `TargetPortfolio` 一次性撮合 |
| 实盘盘中 | `ops/modes._intraday_buy_block` | T+1 作战池 → `compose_intraday_alpha` → 最新价买入 |
| 盘中代理回测 | `intraday_timing.py` | 日 K 代理 spot；`speed` 用 `(close-open)/open*100` 近似（R24） |
