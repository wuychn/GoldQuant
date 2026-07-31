# GoldQuant 文档索引

> 本文档体系基于**当前代码**整理，面向使用与维护。  
> 本仓库仅做数据聚合与纸面模拟交易辅助，**不构成投资建议**。

## 快速导航

| 文档 | 内容 |
|------|------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 系统定位、分层架构、选股→买卖闭环、数据层与数据源抽象、候选池与 LLM 分工 |
| [FACTORS.md](./FACTORS.md) | 20 个日频因子 + 5 个盘中因子：含义、公式、方向、权重、数据来源 |
| [OPERATIONS.md](./OPERATIONS.md) | 安装、启动 API/量化机器人、调度、离线库、回测、研究脚本 |
| [CONFIG.md](./CONFIG.md) | `.env` 环境变量 + `quant.yml` 配置项含义与覆盖方式；配置键→代码映射 |
| [API.md](./API.md) | FastAPI 数据服务路由概览 |

## 一句话概览

GoldQuant 是 A 股日频波段量化辅助系统，由 **FastAPI 数据聚合服务**（`app/`）与 **量化决策内核**（`quant/`）组成：

- **T 晚**：日频因子选股 → 生成作战池与卖出监控 → 飞书推送
- **T+1 盘中**：盘中因子择时买入 + 止损监控卖出 → 纸面撮合
- **离线**：Parquet 日线库支撑因子、回测与决策；IC/walk-forward 驱动因子权重

## 运行时目录

默认数据根：`~/.quant`（可用 `GOLDQUANT_QUANT_HOME_DIR` 或 `QUANT_HOME` 覆盖）

```text
~/.quant/
├── data/              # 离线 Parquet 库（daily_raw、adj、universe 等）
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

```powershell
# 启动数据 API（须先于 quant 运行）
python -m app

# 日决策（T 晚选股）
python -m quant daily_decision

# 盘中择时买卖
python -m quant during_market

# 离线库维护
python -m scripts.data.maintain

# 回测
python -m scripts.backtest.run --start 2024-01-01 --end 2024-06-30 --max-positions 10
```

详细说明见 [OPERATIONS.md](./OPERATIONS.md)。
