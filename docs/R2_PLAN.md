# GoldQuant R2 重构计划

> 版本：R2 | 分支：`r2` | 目标：环境→板块→双池→买卖 闭环 + ML 全量数据训练

---

## 1. 背景与问题

R1 以 **100 分评分 + optional/observe 单轨** 为中心，导致：

- 加自选偏晚（多在加速末段才达标）
- 板块是加分项而非阀门
- 买卖标准与池子逻辑脱节
- ML 仅用 `scores_watchlist` 九维分，未用大盘/板块/个股 raw

R2 **重写策略内核**，保留：`app/api` 数据采集、`stock_enrich`、模拟成交、`daily/raw` 归档。

---

## 2. R2 目标

| 目标 | 验收 |
|------|------|
| 控制流 | 环境 → 板块 → 跟踪池 → 作战池 → 买点 → 卖点 |
| 双池 | tracking 永不买；combat ≤12 唯一可买 |
| 数据 | raw 为唯一事实源；ML ETL 出 Parquet |
| ML | 市场/板块/个股特征 + walk-forward + shadow/gate/rank |
| 验证 | 按强/震/弱市分列胜率、盈亏、回撤 |
| 代码 | `quant/r2/` 自包含，无 R1 决策依赖 |

**说明**：历史样本约 32 交易日，R2 验证报告会给出真实指标；「各市场稳定盈利」以 **walk-forward 与 regime 分列** 衡量，不虚构业绩。

---

## 3. 架构

```
app/api + enrich          quant/r2/                    quant/execution
     │                         │                              │
     ▼                         ▼                              ▼
 daily/raw/*.json  ──►  orchestrator.run_mode  ──►  execute_signals
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
    market/regime      sector/engine          pool/manager
         │                    │                    │
         └────────────────────┴────────────────────┘
                              │
                    signals/pipeline
                              │
                    ml/runtime (gate/rank)
```

### 3.1 目录

```
quant/r2/
  config/r2.yml           # R2 唯一策略配置
  domain/models.py        # Regime, SectorRow, PoolMember, R2Signal
  io/payload.py             # 从 API payload 抽取字段
  io/state.py               # ~/.quant/state/r2/
  market/regime.py
  sector/engine.py
  sector/defensive.py
  pool/universe.py
  pool/manager.py
  signals/entry.py
  signals/exit.py
  signals/pipeline.py
  pipeline/phases.py
  ml/etl.py
  ml/features.py
  ml/labels.py
  ml/train.py
  ml/runtime.py
  backtest/engine.py
  backtest/validate.py
  orchestrator.py
  tests/
```

### 3.2 状态文件

```
~/.quant/state/r2/
  tracking_pool.json      # 跟踪池 30～50
  combat_pool.json        # 作战池 8～12
  sector_snapshot.json    # 当日板块引擎输出
  regime.json
~/.quant/ml/
  features/*.parquet
  models/*.joblib
  config/ml_runtime.yml
```

---

## 4. 策略规则（数值化）

### 4.1 环境 Regime

| 档位 | 条件（投票） | 影响 |
|------|--------------|------|
| 强势 | 涨停≥60 且涨跌比优 且指数>0.5% | combat 上限 12，允许 B 类买点 |
| 震荡 | 中间 | 上限 10 |
| 弱势 | 涨停<30 或下跌>上涨 | **禁止新进 combat**；仅减仓 |

### 4.2 板块 Sector Engine

- **输入**：THS 概念/行业四榜（payload）、concept_tracker 窗口
- **生命周期**：萌芽（首入榜≤2日）/ 扩散（3～5日+资金）/ 高潮（涨幅>5%+广度）/ 退潮（跌出榜或资金转负）
- **防御过滤**：银行/证券/保险 + 指数跷跷板 → `eligible=false`
- **输出**：`eligible` 板块列表；仅 eligible 下个股可进 tracking

### 4.3 双池

| 池 | 入 | 出 | 买入 |
|----|----|----|------|
| tracking | 启动段结构 + 板块 eligible + 人气/形态 | 板块退潮、结构破 | **否** |
| combat | tracking≥1日 + 结构确认 + ML/规则排序 Top | 卖出后降级或板块退潮 | **是** |

### 4.4 买点（仅 combat）

- **A 回调企稳**：MA 多头 + 回踩 MA10～20 + intraday 确认
- **B 上升途中**：发散 + MA5 上 + intraday；弱势禁 B

三确认：复用 `quant.trading.confirmation`（R2 pipeline 调用）。

### 4.5 卖点

1. 止损 -7%（近涨停豁免）
2. 移动止盈
3. 逻辑证伪（板块退潮 / 跌出 combat 条件）
4. 趋势衰竭（破 MA5/MA10/MA20）

---

## 5. ML

### 5.1 ETL（`python -m quant.r2.ml.etl`）

| 表 | 粒度 | 特征 |
|----|------|------|
| market_daily | 1/日 | 指数、赚钱效应、涨停高度 |
| sector_daily | 1/日/板块 | 四榜排名、在榜天数、涨跌幅、净流入 |
| stock_daily | 1/日/股 | MA 结构、资金、人气、concept、规则分 |

数据源：`~/.quant/daily/*/raw/*.json`

### 5.2 标签

| 列 | 定义 |
|----|------|
| label_pnl | 6 日内卖出 realized PnL>0 |
| label_fwd5 | 5 日 max 涨−回撤 达标 |
| label_sector_stay | 3 日后仍在涨幅 Top10 |

### 5.3 任务

1. **regime** — 环境分类
2. **sector_fade** — 板块退潮概率
3. **stock_rank** — 作战池内排序 / meta-label 过滤 buy

### 5.4 落地模式

```yaml
# ~/.quant/config/ml_runtime.yml
mode: gate          # shadow | gate | rank
min_prob: 0.52
```

- **shadow**：只写 `derived/ml_scores.json`
- **gate**：规则 signal 且 ML prob ≥ min_prob 才执行
- **rank**：combat 内按 ML 分排序

### 5.5 训练

```bash
python -m quant.r2.ml.etl --from 2026-06-16
python -m quant.r2.ml.train --task stock_rank
python -m quant.r2.ml.train --task sector_fade
```

walk-forward：按交易日 80/20 或滚动 5 折。

---

## 6. 运行节奏

| 模式 | R2 行为 |
|------|---------|
| pre_market | regime + 持仓/combat 竞价巡检；**无四榜、无加池** |
| during_market | entry/exit + 三确认 + 成交 + ML shadow/gate |
| post_market_evening | sector 全量 + universe + 池升降 + 写 state |
| post_market_lunch | 可选 combat 巡检 |

CLI：

```bash
python -m quant pre_market
python -m quant during_market
python -m quant post_market_evening
python -m quant.r2.backtest.validate
python -m quant.r2.ml.etl
python -m quant.r2.ml.train
```

---

## 7. 验证

`quant/r2/backtest/validate.py`：

1. 按日：evening 更新池 → during 快照回放
2. 指标：胜率、盈亏比、max DD、profit factor
3. **按 regime 分列**（强/震/弱）
4. ML：对比 rules-only vs rules+gate

---

## 8. 实施里程碑

| 阶段 | 内容 | 状态 |
|------|------|------|
| R2-0 | 本文档 + r2 分支 | ✅ |
| R2-1 | domain/io/market/sector/pool | ✅ |
| R2-2 | signals + pipeline + orchestrator | ✅ |
| R2-3 | ML etl/train/runtime | ✅ |
| R2-4 | backtest/validate + pytest | ✅ |
| R2-5 | main 入口切换；R1 orchestrator 退役 | ✅ |

---

## 7.1 历史验证结果（2026-07-22，33 交易日 raw）

数据根：`D:\ProgramData\.quant`，命令：`python -m quant r2_validate`

| 指标 | rules-only (shadow) | ML gate |
|------|-------------------|---------|
| 成交笔数 | 3 | 0 |
| 卖出笔数 | 1 | 0 |
| 胜率 | 0% | — |
| 已实现盈亏 | -44,444 | 0 |
| 最大回撤 | 49.1% | 0 |
| 总收益 | -44.5% | 0 |

**按 regime（rules-only）**

| 环境 | 交易日 | 卖出 | 胜率 | 盈亏 |
|------|--------|------|------|------|
| 震荡 | 21 | 1 | 0% | -44,444 |
| 强势 | 5 | 0 | — | 0 |

**ML 训练**：stock_rank 样本 10,540 / 测试 2,636，test_acc ≈ 61.1%。ETL：market 837、sector 1354、stock 31,167 行。

**结论（诚实说明）**

1. 当前约 33 天样本 **不足以** 得出「强/震/弱稳定盈利」结论；验证链路已跑通，报告落盘于 `~/.quant/ml/r2_validation_report.json`。
2. rules-only 在震荡市产生少量成交但样本内亏损；强势窗口内无完成卖出回合。
3. ML gate 模式下模型概率普遍低于 `min_prob=0.52`，全部拦截买入——符合 gate 设计，需更多样本或调低阈值/校准后再启用 gate。
4. 本次修复项：作战池 enrich 快照、震荡市误拦 ascent、回测 config patch、R2 独立 verify、labels ETL 加速。

---

## 9. R1 代码说明

- **R1 完整决策代码保留在 `r1` 分支**（含 ScoringEngine、watchlist、R1 orchestrator 等）。
- **`r2` 分支已删除 R1 决策路径**；仅保留 R2 运行时与 `app/` 数据采集所需的共享模块。
- 删除范围示例：`scoring/engine.py`、R1 `signals/buy|sell|pipeline`、`store/watchlist`、`backtest/engine.py`（R1）、`quant/ml/`（R1 ML）、R1 推送/周报任务等。
- **仍保留**：`candidates/candidate_*`（API enrich）、`scoring/`（payload/主题/别名）、`trading/`（三确认+卖规则）、`gates/rules`（brief 用）等。
- **`quant/pool/` 已改名为 `quant/candidates/`**，与 `quant/r2/pool/`（作战/跟踪池）区分。
- **`quant/orchestrator.py` 转发层已删除**；入口统一 `quant.r2.orchestrator`。

---

## 10. 风险

- 样本少：ML 以 LightGBM + 强正则；报告标注置信区间
- 双源命名：THS 共振 vs EM 结构分轨，不硬映射概念名
- 盈利保证：系统输出 **可复现验证报告**，不承诺实盘收益
