# GoldQuant 文档索引

> 本文档体系基于**当前代码**整理，面向使用与维护。  
> 本仓库仅做数据聚合与纸面模拟交易辅助，**不构成投资建议**。

## 快速导航

| 文档 | 内容 |
|------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 系统定位、分层架构、选股→买卖闭环、数据层与数据源抽象、候选池与 LLM 分工 |
| [FACTORS.md](./FACTORS.md) | 20 个日频因子 + 5 个盘中因子：含义、公式、方向、权重、数据来源 |
| [OPERATIONS.md](./OPERATIONS.md) | 从零建库、合并/补缺/校验、日常 update/maintain、回测、定时任务与参数表 |
| [DAILY_OPS.md](./DAILY_OPS.md) | 数据完备后：每日选股、纸面模拟交易、调度闭环 |
| [CONFIG.md](./CONFIG.md) | `.env` 环境变量 + `quant.yml` 配置项含义与覆盖方式；配置键→代码映射 |
| [API.md](./API.md) | FastAPI 数据服务路由概览 |

## 一句话概览

GoldQuant 是 A 股日频量化辅助系统：**动量双槽纸面交易** + FastAPI 调度/推送。

**怎么用**（细节与命令见根目录 [README.md「怎么用」](../README.md#怎么用)）：

1. `poetry install`，`.env` 写 `GOLDQUANT_QUANT_HOME_DIR`
2. **收集数据**：首次 `build_daily` + `backfill_daily_meta` + `validate_library`；每天盘后 `update_daily`；每周 `maintain`
3. **回测**：`python -m scripts.backtest.run_momentum`（与纸面同一套规则）
4. **纸面**：T 晚 `daily_decision` 写作战池 → T+1 盘中 `during_market` 开盘买、到期尾盘卖
5. **无人值守**：`python -m app`（18:00 增量 / 20:10 选股 / 盘中买卖）

当前策略：昨日收盘涨幅 Top6 + 沪深300>MA55 + 双槽；`momentum_swing.enabled: false` 才走 IC + 盘中 θ。

## 运行时目录

默认数据根：`~/.quant`（可用 `GOLDQUANT_QUANT_HOME_DIR` 或 `QUANT_HOME` 覆盖）

```text
~/.quant/
├── store/             # 离线 Parquet 库（daily_raw、adj、universe 等）
├── state/             # 人工持仓 account.json / holding.jsonl
├── paper_account/     # 纸面账户（与人工仓隔离）
│   ├── battle_pool/   # 作战池
│   ├── sell_watch/    # 卖出监控
│   └── state/         # 纸面持仓与权益
├── config/            # 用户覆盖 quant.yml、factor_weights*.yml
├── daily/{date}/      # 按日归档 raw/derived/trades/review
├── reports/           # 决策/回测/IC 报告
└── memory/            # 新闻摘要等
```

## 常用命令

依赖用 Poetry 安装：`poetry install`（详见 [OPERATIONS.md](./OPERATIONS.md)）。命令前缀统一为 `poetry run`。

```powershell
# 启动数据 API + 调度（库已建好、要无人值守时）
poetry run python -m app

# 收集数据
poetry run python -m scripts.data.build_daily --home D:\ProgramData\.quant --start 2021-01-01
poetry run python -m scripts.data.update_daily --home D:\ProgramData\.quant
poetry run python -m scripts.data.validate_library --home D:\ProgramData\.quant

# 动量回测（与纸面同规则）
poetry run python -m scripts.backtest.run_momentum --home D:\ProgramData\.quant

# T 晚选股 / T+1 盘中纸面
poetry run python -m quant daily_decision
poetry run python -m quant during_market
```

- 从零建库与参数表：[OPERATIONS.md](./OPERATIONS.md)  
- 每日选股与模拟交易：[DAILY_OPS.md](./DAILY_OPS.md)
