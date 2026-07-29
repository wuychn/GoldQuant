# 配置说明（quant.yml）

策略参数、门禁、调度、数据源等**不在 `.env`**，统一由 YAML 配置。

## 1. 配置文件位置

| 优先级 | 路径 | 说明 |
|---|---|---|
| 1（覆盖） | `~/.quant/config/quant.yml` | 用户级，deep merge 包内默认 |
| 2（默认） | `quant/config/quant.yml` | 仓库内置 |

加载：`quant/config.py:load_quant_config()`，经 `quant/yml_schema.py` Pydantic 校验（顶层 `extra=forbid`，未知键报错）。

因子权重单独文件（非 quant.yml）：

| 文件 | 优先级 | 说明 |
|---|---|---|
| `~/.quant/config/factor_weights_ts.yml` | 最高 | walk-forward OOS 时变权重 |
| `~/.quant/config/factor_weights.yml` | 次之 | 静态 IC 权重 |
| `REGISTRY.weights()` | 回退 | 代码内 default_weight |

---

## 2. gates — 交易门禁与模拟规则

### gates.trading

| 键 | 默认 | 含义 |
|---|---|---|
| `time_validation_enabled` | true | 是否校验交易时段（联调可 false） |
| `daily_loss_limit_pct` | -3.0 | 日内权益回撤禁开仓 |
| `stoploss_cooldown_days` | 3 | 止损后冷却天数 |
| `block_same_day_rebuy_after_sell` | true | 当日卖出后禁止买回 |
| `simulation.commission_rate` | 0.0001 | 佣金率（万一） |
| `simulation.min_commission` | 5.0 | 最低佣金（元） |
| `simulation.stamp_tax_rate` | 0.0005 | 卖出印花税 |
| `simulation.transfer_fee_rate` | 1e-5 | 沪市过户费 |
| `simulation.slippage_pct` | 0.001 | 固定滑点（备用） |
| `simulation.slippage_model` | sqrt_law | 滑点模型 |
| `simulation.slippage_sqrt_k` | 0.5 | sqrt 滑点系数 |
| `simulation.slippage_max_pct` | 0.008 | 滑点上限 |
| `simulation.partial_fill_enabled` | true | 部分成交 |
| `simulation.participation_rate` | 0.1 | ADV 参与率 |

涨跌停幅度由 `quant/backtest/tradability.py` 按板块/ST 自动判定，**不在 yml 配置**。

### gates.symbol_pool

| 键 | 默认 | 含义 |
|---|---|---|
| `prefixes` | 60, 00, 30 | 允许的股票代码前缀 |
| `exclude_st` | true | 排除 ST |

### gates.circuit_breaker

| 键 | 默认 | 含义 |
|---|---|---|
| `circuit_breaker.index_drop_pct` | -2.0 | 指数跌幅熔断（禁开仓） |

### gates.sell

| 键 | 默认 | 含义 |
|---|---|---|
| `limit_up_exempt_margin_pct` | 0.5 | 涨停豁免边际 |
| `limit_down_approach_margin_pct` | 1.0 | 跌停接近边际 |
| `late_session_after` | 14:30 | 尾盘时段起点 |
| `late_session_final_only` | true | 尾盘仅强卖信号 |
| `late_session_kinds` | 破5日线, 趋势衰竭 | 尾盘允许卖的信号类型 |

---

## 3. portfolio — 组合层

| 键 | 默认 | 含义 |
|---|---|---|
| `optimizer` | mvo | 配权：mvo / rank_vol |
| `optimizer_mvo.risk_aversion` | 2.0 | MVO 风险厌恶 λ |
| `covariance.method` | ewma | 协方差：ewma / shrink |
| `risk_budget.vol_lookback` | 14 | 波动/协方差窗口 |
| `constraints.max_concept_pct` | 40 | 单概念暴露上限(%) |
| `constraints.max_industry_pct` | 40 | 单行业暴露上限(%) |
| `style_exposure.alpha_weighted` | true | alpha 强度配权（MVO 分支可能不生效） |
| `style_exposure.alpha_shrink` | 0.4 | alpha 配权收缩 |
| `style_exposure.max_small_cap_pct` | 50 | 小市值桶上限 |
| `style_exposure.max_high_mom_pct` | 60 | 高动量桶上限 |
| `risk.max_drawdown_halt_enabled` | true | 组合回撤停机 |
| `risk.max_drawdown_pct` | 15 | 回撤阈值(%) |
| `risk.halt_days` | 5 | 停机天数 |

**CLI 可覆盖**（`scripts/decision/daily.py` / `scripts/backtest/run.py`）：`n_enter=8`, `n_exit=15`, `max_positions=10`, `target_vol=0.15`。

---

## 4. research — 因子权重

| 键 | 默认 | 含义 |
|---|---|---|
| `factors.strict_oos_weights` | true | live 禁用全样本静态 IC 权重 |

IC 持有期、FDR、晋升门槛等由 `scripts/factors/fit_weights.py`、`scripts/research/walk_forward.py` 的 CLI 参数控制，不在 yml 中配置。

---

## 5. candidate — 候选池（展示用）

| 键 | 默认 | 含义 |
|---|---|---|
| `popularity_limit` | 20 | 人气榜取前 N |
| `zt_min_boards` | 3 | 涨停池最少连板 |
| `cxg_labels` | 创月/半年/一年/历史新高 | 创新高标签 |
| `include_zt_pool` | false | 是否含涨停池 |
| `include_ths_rank_pool` | true | 是否含同花顺榜 |
| `universe.min_adv_yi` | 1.0 | 最小 ADV（亿元） |
| `universe.adv_lookback` | 20 | ADV 回看天数 |
| `universe.max_crowding_rank` | null | 人气过热排名阈值 |
| `universe.participation_rate` | 0.1 | 流动性参与率 |

---

## 6. data — 数据源与限流

| 键 | 默认 | 含义 |
|---|---|---|
| `default_max_concurrent` | 1 | 默认并发 |
| `akshare_max_concurrent` | 1 | AKShare 并发 |
| `ths_max_concurrent` | 1 | 同花顺并发 |
| `eastmoney_max_concurrent` | 1 | 东财并发 |
| `sources.spot` | akshare | 现货数据源（akshare / eastmoney / fixture） |

数据源工厂：`quant/data/sources/factory.py`；限流：`quant/data/sources/rate_limit.py`。

---

## 7. scheduler — 定时任务

| 键 | 默认 | 含义 |
|---|---|---|
| `enabled` | true | 启动 API 时注册 APScheduler |
| `timezone` | Asia/Shanghai | 时区 |
| `news_hours` | 8–22 | 新闻 cron 小时 |
| `news_minute` | 0 | 新闻 cron 分钟 |
| `pre_market_time` | 09:25 | 盘前 |
| `during_market_times` | 09:37 起每 7 分钟… | 盘中（见 `quant/scheduler/times.py`） |
| `post_market_lunch_time` | 11:50 | 午间 |
| `maintain_daily_time` | 16:00 | 数据维护 |
| `daily_decision_time` | 20:10 | 日决策 |
| `prefetch_concepts_enabled` | true | 预取概念 |
| `prefetch_concepts_time` | 05:00 | 预取时点 |
| `misfire_grace_sec` | 600 | 错过任务的宽限秒数 |
| `in_process_modes` | news, pre_market, … | 进程内轻量模式（不走子进程 HTTP） |

读取：`quant/scheduler/config.py` → `app/scheduling/quant_scheduler.py`。

---

## 8. 配置键 → 代码映射（维护用）

| 配置段 | 主要读取位置 |
|---|---|
| `gates.trading` | `quant/config.trading_time_checks_enabled`, `quant/execution/sim_rules.py`, `quant/backtest/costs.py` |
| `gates.symbol_pool` | `app/utils/common_util`, `quant/pool/symbol_filter.py` |
| `gates.sell` | `quant/exit/rules.py`, `quant/trading_hours.py` |
| `gates.circuit_breaker` 等 | `quant/execution/risk_gate.py` |
| `portfolio` | `quant/portfolio/target.py:TargetPortfolio.from_config` |
| `research.factors.*` | `quant/config.load_factor_weights_info` |
| `candidate` | `quant/pool/candidate_sources.py`, `quant/pool/liquidity.py` |
| `data` | `quant/data/sources/rate_limit.py`, `factory.py` |
| `scheduler` | `quant/scheduler/config.py`, `app/scheduling/quant_scheduler.py` |

校验 schema：`quant/yml_schema.py`（强校验 `data.*`、`scheduler.*`、`research.factors.*` 等）。

---

## 9. 已迁出 `.env` 的配置

以下曾用环境变量，**现已改 quant.yml**：

- `QUANT_SCHEDULER_ENABLED` → `scheduler.enabled`
- `QUANT_SCHED_*` → `scheduler.*`
- 策略阈值、门禁、组合参数 → `gates` / `portfolio` / `research`

环境变量仅保留密钥、网络、运行时开关，见 [ENV.md](./ENV.md)。
