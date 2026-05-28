# GoldQuant

A 股短线量化辅助系统：**FastAPI 数据聚合服务** + **评分引擎决策机器人** + **LLM 复盘叙述** + **飞书推送** + **ML 离线校准**。

> 本仓库仅做数据聚合与模拟交易辅助，**不构成投资建议**。行情来自 AKShare / 东财 / 同花顺等第三方，存在延迟、字段变更或访问失败的可能。

---

## 架构概览

```text
┌─────────────────────────────────────────────────────────────┐
│  app/  数据 API（FastAPI，默认 :8085）                        │
│  quant_endpoint.py → 新闻 / 盘前 / 盘中 / 午间 / 晚间 JSON    │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP
┌───────────────────────────▼─────────────────────────────────┐
│  quant/  决策机器人（python -m quant <mode>）                 │
│  评分引擎 + 硬门禁 → 买卖信号 → 模拟成交 → LLM 叙述 → 飞书    │
└───────────────────────────┬─────────────────────────────────┘
                            │ 离线
┌───────────────────────────▼─────────────────────────────────┐
│  quant/ml  读取 ~/.quant/daily 历史 → 校准阈值/权重           │
└─────────────────────────────────────────────────────────────┘
```

**职责划分**

| 组件 | 做什么 | 不做什么 |
|------|--------|----------|
| 评分 + 门禁 | 加自选、买卖、仓位 | — |
| LLM | 解读数据、写复盘/推送文案 | 不参与下单决策 |
| ML | 离线优化阈值与维度权重 | 盘中不推理 |

---

## 项目结构

```text
GoldQuant/
├── app/                        # FastAPI 数据服务
│   ├── main.py
│   └── api/v1/endpoints/
│       ├── quant_endpoint.py   # 量化五时段聚合接口（核心）
│       └── ...                 # 其他行情/热度接口
├── quant/                      # 量化决策机器人
│   ├── orchestrator.py         # 五模式编排
│   ├── config/                 # scoring.yml / gates.yml 默认配置
│   ├── scoring/                # 100 分制评分引擎
│   ├── gates/                  # 硬门禁（T+1、熔断、标的池…）
│   ├── signals/                # 买卖信号
│   ├── execution/              # 模拟成交
│   ├── narrative/              # LLM 叙述
│   ├── push/                   # 飞书推送
│   ├── ml/                     # ML 离线校准
│   └── strategy.md             # 策略条文（人工维护）
├── data/                       # 接口返回样例 JSON（离线调试）
├── requirements.txt
├── pyproject.toml
└── README.md
```

**运行时数据目录**（自动创建）：`~/.quant/`

```text
~/.quant/
├── state/          # optional.jsonl、holding.jsonl、account.json（程序读写）
├── views/          # optional.md、holding.md（自动生成，勿手改）
├── daily/{date}/   # raw/ derived/ trades/ review/
├── config/         # scoring.yml、gates.yml、ml_calibration.yml
└── memory/         # 新闻摘要、经验教训
```

---

## 环境要求

- Python **3.10+**（推荐 3.11）
- 可访问外网（拉取行情）
- Windows / Linux 均可

---

## 一、安装

在项目根目录 `GoldQuant` 下：

```powershell
# 创建并激活虚拟环境（PowerShell）
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 安装依赖（含量化 + ML）
pip install -r requirements.txt

# 或仅安装 ML 可选包
# pip install -e ".[ml]"
```

复制环境变量模板：

```powershell
copy .env.example .env
```

编辑 `.env`，至少配置：

| 变量 | 说明 |
|------|------|
| `GOLDQUANT_PORT` | API 端口，默认 `8085` |
| `LLM_API_KEY` | LLM 密钥（复盘叙述） |
| `LLM_BASE_URL` | LLM 接口地址 |
| `LLM_MODEL` | 模型名 |
| `FEISHU_APP_ID` | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 飞书应用密钥 |
| `FEISHU_USER_ID` | 飞书接收人 open_id |

---

## 二、启动数据 API

**必须在项目根目录执行**，且已激活 venv。

```powershell
# 推荐
python -m app

# 或
uvicorn app.main:app --host 0.0.0.0 --port 8085
```

- 文档：<http://127.0.0.1:8085/docs>
- 健康检查：<http://127.0.0.1:8085/health>

Linux 后台常驻：

```bash
chmod +x run.sh
./run.sh start
```

> **注意**：量化机器人通过 `http://localhost:8085` 拉数据，**须先启动 API**，再跑 `python -m quant`。

---

## 三、运行量化机器人

### 3.1 五种模式

| 命令 | 时段 | 自选 | 买卖 | 说明 |
|------|------|------|------|------|
| `python -m quant news` | 任意 | — | — | 新闻解读 + 飞书 |
| `python -m quant pre_market` | 盘前 | 不改 | 可买 | 开盘分析 + 操作段 |
| `python -m quant during_market` | 盘中 | 不改 | 可买卖 | 盘中监控 + 操作段 |
| `python -m quant post_market_lunch` | 午间 | **不更新** | — | 上午复盘 |
| `python -m quant post_market_evening` | 晚间 | **评分达标新增** | — | 全天复盘 + 自选更新 |

### 3.2 单次执行示例

```powershell
# 1. 确保 API 已启动
python -m app

# 2. 另开终端，激活 venv 后执行（示例：晚间复盘）
python -m quant post_market_evening
```

每次运行会：拉取 API 数据 → 落盘到 `~/.quant/daily/` → 评分/交易/叙述 → 推送飞书。

### 3.3 建议调度（cron / 任务计划）

| 时间 | 模式 |
|------|------|
| 08:00 起多次 | `news` |
| 09:20 | `pre_market` |
| 09:35～14:30 每 10～30 分钟 | `during_market` |
| 11:50 | `post_market_lunch` |
| 15:10 | `post_market_evening` |

Windows 任务计划或 Linux crontab 调用同一命令即可；工作目录设为项目根，并激活 venv。

### 3.4 飞书推送格式

不变，示例：

```text
【晚间复盘】2026-05-26 15:10:00

一、全天大盘
…

九、自选更新
【新增自选】
· 某某股份（600xxx）评分72.5 [涨停板战法]
```

「操作」「自选更新」段由**评分引擎 + 执行器**确定性产出；其余段落由 LLM 叙述。

---

## 四、配置说明

### 4.1 评分与阈值

默认：`quant/config/scoring.yml`

用户覆盖：`~/.quant/config/scoring.yml`

主要字段：

```yaml
watchlist_threshold: 65   # 加自选最低分
buy_threshold: 72         # 买入最低分
sell_threshold: 45        # 低于此考虑卖出
dimensions:               # 各维度 enabled + weight
  market_index: { enabled: true, weight: 12 }
  ...
```

### 4.2 硬门禁与仓位

默认：`quant/config/gates.yml`  
用户覆盖：`~/.quant/config/gates.yml`

含：标的池过滤、极端熔断、每日亏损限额、止损冷却、分市场状态的仓位上限等。

`trading.time_validation_enabled: false` 时任意时刻可模拟成交（便于联调）；实盘请改为 `true`。

### 4.3 策略文档

`quant/strategy.md` 为策略条文源；LLM 叙述时会注入相关章节，**买卖不由 LLM 决定**。

---

## 五、ML 离线校准

ML **不参与盘中推理**，仅在收盘后（或周末）用历史数据优化阈值与维度权重。

### 5.1 数据来源

自动扫描 `~/.quant/daily/*/derived/scores_watchlist.json`，结合：

- 次日行情涨幅（`daily/{next}/raw/*.json`）
- 后续成交盈亏（`daily/*/trades/executed.json`）

构建标签后做校准。**至少积累约 20 条样本**后再跑（默认 `--min-samples 20`）。

### 5.2 命令

```powershell
# 预览结果（不写文件）
python -m quant.ml calibrate --method grid --dry-run

# 网格搜索阈值（默认）
python -m quant.ml calibrate --method grid --apply

# 线性回归 → 维度权重 + 网格阈值
python -m quant.ml calibrate --method linear --apply

# LightGBM 特征重要性 → 维度权重
python -m quant.ml calibrate --method lightgbm --apply

# 贝叶斯优化（scipy differential_evolution）→ 阈值
python -m quant.ml calibrate --method bayesian --apply
```

### 5.3 生效方式

`--apply` 写入 `~/.quant/config/ml_calibration.yml`，下次 `python -m quant` 启动时自动合并到评分配置（优先级高于包内默认值）。

文件示例：

```yaml
generated_at: "2026-05-26 20:00:00"
method: grid
sample_count: 45
apply: true
thresholds:
  watchlist_threshold: 65
  buy_threshold: 75
  sell_threshold: 42
dimension_weights:   # linear / lightgbm 时有
  popularity_rank: 12.5
  concept_theme: 14.0
metrics:
  f1: 0.58
```

取消 ML 覆盖：删除该文件或将 `apply: false`。

---

## 六、量化数据 API（OpenClaw 入口）

前缀 **`/api/v1`**，核心路由在 `quant_endpoint.py`：

| 说明 | 方法 | 路径 |
|------|------|------|
| 新闻 | GET | `/api/v1/quant/market/news` |
| 盘前 | GET | `/api/v1/quant/market/pre_market` |
| 盘中 | GET | `/api/v1/quant/market/during_market` |
| 午间复盘 | GET | `/api/v1/quant/market/post_market_lunch` |
| 晚间复盘 | GET | `/api/v1/quant/market/post_market_evening` |

响应格式：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "大盘指数": [],
    "赚钱效应": {},
    "自选股": [],
    "持仓股": []
  }
}
```

自选股 / 持仓从 `~/.quant/state/optional.jsonl`、`holding.jsonl` 读取并 enrich。

`data/` 目录下为各接口样例，可用于离线阅读字段结构。

### 其他行情 API

| 说明 | 路径 |
|------|------|
| 东财人气榜 | `/api/v1/hot/eastmoney/popularity` |
| 同花顺热榜 | `/api/v1/hot/ths` |
| 东财个股资讯 | `/api/v1/news/em?symbol=` |

完整列表见 <http://127.0.0.1:8085/docs>。

---

## 七、常见问题

**Q：quant 报连接失败？**  
A：先确认 `python -m app` 已启动，且 `quant/config.py` 中 `BASE_URL` 与 API 端口一致（默认 `http://localhost:8085`）。

**Q：旧版 `~/.quant/optional.jsonl` 在哪？**  
A：已改为 `~/.quant/state/optional.jsonl`，请手动迁移或重新跑晚间复盘生成自选。

**Q：ML 提示样本不足？**  
A：多运行若干交易日，确保每天晚间复盘产生 `daily/{date}/derived/scores_watchlist.json`。

**Q：`.env` 端口不生效？**  
A：使用 `python -m app` 启动；裸 `uvicorn` 需显式 `--port`，见 `.env.example` 说明。

---

## 八、本地自检

```powershell
curl http://127.0.0.1:8085/health
curl http://127.0.0.1:8085/api/v1/quant/market/pre_market
python -m quant.ml calibrate --method grid --dry-run
```

---

## 许可证与致谢

- 项目代码以仓库许可为准。  
- 数据版权归各提供方所有；感谢 [AKShare](https://github.com/akfamily/akshare)。
