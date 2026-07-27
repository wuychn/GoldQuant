# GoldQuant

A 股日频波段量化辅助系统：**FastAPI 数据聚合服务** + **因子评分/目标组合决策** + **纸面撮合** + **LLM 复盘叙述** + **飞书推送** + **ML 离线校准**。

> 本仓库仅做数据聚合与纸面模拟交易辅助，**不构成投资建议**。行情来自 AKShare / 东财 / 同花顺等第三方，存在延迟、字段变更或访问失败的可能。

> 架构、因子/组合/出场指标与交易闭环见 **[docs/R3_ARCHITECTURE.md](docs/R3_ARCHITECTURE.md)**；运维 CLI / 落盘 / 飞书见 **[docs/R3_OPS.md](docs/R3_OPS.md)**。

---

## 架构概览

```text
┌─────────────────────────────────────────────────────────────┐
│  app/   数据 API（FastAPI，默认 :8085）                        │
│         五时段聚合接口 + 候选池 enrich                          │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP
┌───────────────────────────▼─────────────────────────────────┐
│  quant/  决策与运维（python -m quant <mode>）                  │
│  L0 数据 → L1 因子 → L2 目标组合 → L3 纸面撮合 → 飞书推送       │
│  L4 时序出场（优先于再平衡）                                    │
└───────────────────────────┬─────────────────────────────────┘
                            │ 离线
┌───────────────────────────▼─────────────────────────────────┐
│  quant/ml  读 ~/.quant/daily 历史 → 校准阈值/权重              │
└─────────────────────────────────────────────────────────────┘
```

**职责划分**

| 组件 | 做什么 | 不做什么 |
|------|--------|----------|
| 因子 + 目标组合 + 撮合 | 决定买卖、仓位（差额交易 + 时序出场） | — |
| LLM | 解读数据、写新闻/复盘文案 | 不参与下单决策 |
| ML | 离线优化阈值与维度权重 | 盘中不推理 |

设计原则：**可回溯优先**（主链路只用 ≤T 信息重建）、**相对优于绝对**（横截面排名而非绝对分数）、**目标组合优于逐笔信号**（每日目标权重，交易差额）、**横截面入场 + 时序出场**（排名处理相对变弱，ATR/破位处理个股崩塌）。

---

## 项目结构

```text
GoldQuant/
├── app/                        # FastAPI 数据服务
│   ├── main.py
│   └── api/v1/endpoints/
│       ├── quant_endpoint.py   # 五时段聚合接口（自选/持仓 enrich）
│       └── mkt_*.py             # 行情/热度/资金流接口
├── quant/                      # r3 量化核心
│   ├── data/                   # L0 离线库、日历、复权、宇宙、行业 PIT
│   ├── store/                  # state/paths/snapshot/views + 自选/观察池
│   ├── factors/                # L1 因子库、中性化、合成 alpha、面板
│   ├── portfolio2/             # L2 目标组合（buffer/约束/vol target）
│   ├── execution/              # L3 成本规则、滑点、撮合器
│   ├── decision/               # 日决策卡 + 纸面撮合入口
│   ├── backtest2/              # L3 回测引擎（broker/metrics/report）
│   ├── exit/                   # L4 时序出场（ATR/硬止损/趋势/时间）
│   ├── ops/                    # 日运维：新闻/盘前/盯盘/复盘 + 推送
│   ├── push/                   # 飞书推送
│   ├── journal/                # 漏斗统计 + 偏离日志（辅助决策闭环）
│   ├── research2/              # walk-forward / DSR / 敏感性（参数治理）
│   ├── ml/                     # 离线校准
│   ├── scoring/ pool/          # 评分维度 + 候选池（被 factors/app 活依赖）
│   ├── strategy/ market/       # 战法工具 + 资金流（被 scoring/narrative 引）
│   ├── signals/{models,sell_policy}  # 信号模型 + 卖出策略（撮合活依赖）
│   ├── narrative/{llm,prompts,...}   # LLM 叙述（运维推送活依赖）
│   └── config.py / timeutil.py / trading_hours.py / data_fetch.py
├── scripts/{decision,backtest,data,factors,research}/
├── docs/R3_ARCHITECTURE.md     # 架构 / 指标 / 交易闭环（主文档）
├── docs/R3_OPS.md              # 运维 CLI / 落盘 / 飞书
└── requirements.txt
```

**运行时数据目录**（自动创建）：`~/.quant/`，纸面账户在 `~/.quant/paper_account/`。报告在 `~/.quant/reports/`。

可通过环境变量覆盖存储位置（优先级从高到低）：

1. `GOLDQUANT_QUANT_HOME_DIR`（写进 `.env`，与其它 `GOLDQUANT_` 前缀一致）；
2. `QUANT_HOME`（shell 临时覆盖，无需前缀）；
3. 默认 `~/.quant`。

> 跨日归档目录（`~/data/quant/archive`）单独由 `GOLDQUANT_QUANT_ARCHIVE_DIR` 控制，二者相互独立。

```text
~/.quant/
├── state/          # optional.jsonl、holding.jsonl、account.json（程序读写）
├── views/          # optional.md、holding.md（自动生成，勿手改）
├── daily/{date}/   # raw/ derived/ trades/ review/
├── config/         # quant.yml（含 gates）、ml_calibration.yml（ML 覆盖）
├── paper_account/  # 纸面账户 state/ + equity.jsonl（与人工仓隔离）
└── memory/         # 新闻摘要、经验教训
```

---

## 环境要求

- Python **3.11+**（推荐 3.11）
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

> **注意**：量化机器人通过 `http://localhost:8085` 拉数据，**须先启动 API**，再跑 `python -m quant`。`QUANT_USE_LOCAL_FIXTURE=true` 时可改读 `data/*.json` fixture，不请求 API。

---

## 三、业务逻辑

系统分两类任务：**日决策闭环**（核心，T-1 信号 / T 开盘纸面成交）与 **五时段运维推送**（盯盘与复盘叙述）。原则：**买卖、仓位均由因子+目标组合+撮合决定**；LLM 只负责新闻解读与复盘叙述，盘中推送正文由模板直接生成。

### 3.1 日决策闭环（`scripts/decision/daily.py` / `python -m quant daily_decision`）

```text
加载 PIT 数据（universe/行业/复权） → 因子面板 ≤T
   → compose_alpha 加权 z-score → 截面排序
   → 持仓时序出场（L4，优先于再平衡）
   → TargetPortfolio 目标权重 w* → w* - w 差额
   → 先卖后买 + 涨跌停/T+1/整手/ADV5% → 纸面账户 state
   → equity.jsonl + 决策报告 + 飞书
```

- **时间口径**：默认 `strict`（T-1 收盘因子 → T 开盘成交，实盘/纸面/默认回测一致）；`loose`（T 收盘因子 → T 收盘，乐观上界对照）。
- **差额交易**：`ΔV_i = (w*_i − w_i) × 总资产`，换算整手股数；`|Δw|` 小于缓冲则不动。
- **决策卡**：产出"今日该做什么"的卡（buy/sell/reduce/add/hold + 出场信号），人根据卡片执行并把实际操作回填到偏离日志；同时默认在 `paper_account/` 下纸面撮合。

### 3.2 五时段运维推送（`quant/ops/`）

| 模式 | 命令 | 默认调度 | 推送正文 |
|------|------|-----------|----------|
| 新闻 | `news` | 8–22 每个整点 | LLM |
| 盘前 | `pre_market` | 09:25 | 模板（指数+纸面账户+关注） |
| 盘中 | `during_market` | 09:37 起每 7 分钟（至 15:00） | 模板（指数+纸面持仓+异动） |
| 午间复盘 | `post_market_lunch` | 11:50 | 模板（午前指数+纸面账户） |
| 收盘复盘 | `post_market_evening` | 20:10 | 模板（收盘指数+纸面绩效+持仓） |

- `news` 走 LLM（`prompt_news`）去噪要点 + 综合解读，摘要落 `~/.quant/memory/` 供盘前引用。
- 其余四时段由 `ops/modes.py` 模板生成（指数 + 纸面账户/持仓 + 涨幅榜），**不**做评分、**不**改自选、**不**交易。
- `daily_decision` 单独推送"日决策·纸面成交"（指令/成交/账户/持仓）。

### 3.3 因子层（L1）

因子在**后复权**序列上计算，仅用 `≤ as_of` 数据。流水线：

```text
raw × direction → winsorize(1%,99%) → 行业+log市值中性 → z-score → Σ w_i z_i = alpha
```

`compose_alpha`（`quant/factors/compose.py`）= 加权 z-score 均值。13 个因子（动量 mom_20/60/120_20/accel、效率比 eff_ratio_60、波动 vol_60/downside_vol_60、距高 dist_high_252、均线 ma_spread/ma_slope_20、放量 vol_ratio_5_20、换手 turnover_z_60、量价 vol_price_corr_20、资金 flow_ratio_5、主题 theme_mom、人气 hot_rank_z）的默认权重与方向见 [R3_ARCHITECTURE.md §5](docs/R3_ARCHITECTURE.md#5-因子层l1指标含义与计算)。

### 3.4 目标组合（L2，`quant/portfolio2/target.py`）

`TargetPortfolio.target_weights` 五步：排名 buffer → 等权/逆波动 → vol target 缩放 → 约束 → 权重缓冲。

| 参数 | 默认 | 含义 |
|------|-----:|------|
| `n_enter` | 8 | 新进门槛（排名 ≤8 才允许新建仓） |
| `n_exit` | 15 | 保留门槛（已持仓排名 ≤15 才保留） |
| `max_stocks` | 10 | 最大持股数 |
| `full_invest` | 0.95 | 目标总仓位 |
| `target_vol` | 0.15 | 组合年化波动目标 |
| `max_weight` | 0.25 | 单票上限 |
| `sector_cap` | 0.40 | 单行业上限 |
| `concept_cap` | 0.40 | 单概念上限 |
| `buffer_abs`/`min_trade` | ~1% | 压换手（偏差过小不调仓） |

排名 buffer：`n_enter < rank ≤ n_exit` 的已持仓不因排名抖动频繁进出。完整说明见 [§6](docs/R3_ARCHITECTURE.md#6-组合层l2参数与计算)。

### 3.5 时序出场（L4，`quant/exit/rules.py`）

出场检查**优先于**再平衡。`evaluate_exits` 并行算四类信号，按优先级取一个：`hard_stop > atr_trailing > trend_stop_ma20 > time_stop`。

| 规则 | reason | 触发条件 |
|------|--------|----------|
| 硬止损 | `hard_stop` | `C ≤ max(入场×0.92, 入场−2×ATR14)`（取较紧者） |
| ATR 跟踪 | `atr_trailing` | `C ≤ max(入场价, 持仓最高收盘) − 3×ATR14` |
| 趋势止损 | `trend_stop_ma20` | 收盘连续 2 日 < MA20 |
| 时间止损 | `time_stop` | 持有 > 20 交易日**且浮盈 < 0** |
| 排名出场 | 经 L2 | 排名 > `n_exit` → 目标权重 0 |

完整说明见 [§7](docs/R3_ARCHITECTURE.md#7-出场层l4规则与计算)。

### 3.6 纸面撮合（L3，`quant/execution/executor.py` + `decision/paper_execute.py`）

`execute_signals` 两阶段，**先卖后买**：

- A 股 T+1（当日买入不可卖）、当日已卖不回补；
- 涨跌停封板拒单；100 股整数倍；可用资金不足跳过买入；
- 佣金万一（最低 5 元）、卖出印花税、沪市过户费、滑点；
- 整手股数：`⌊amount/price/100⌋×100`；单日成交额 ≤ 当日 `amount×5%`（ADV）。

纸面账户隔离：`paper_home_context` 把 state 根切到 `{quant_home}/paper_account/`，与人工主账户互不污染。回测（`backtest2`）与实盘纸面共享同一撮合内核与 `TargetPortfolio`/`evaluate_exits`。

### 3.7 决策与叙述分工

| 内容 | 产出方 |
|------|--------|
| 目标权重、买卖、成交、仓位 | **因子+目标组合+撮合引擎** |
| 盘中/盘前/午间/收盘推送正文 | **模板** `ops/modes.py` |
| 新闻解读、复盘叙述 | **LLM**（注入 `engine_brief`，不得推翻引擎结论） |
| 阈值/权重离线优化 | **ML**（不参与盘中推理） |

---

## 四、运行量化机器人

### 4.1 命令速查

```powershell
# 运维推送（五时段）
python -m quant news
python -m quant pre_market
python -m quant during_market
python -m quant post_market_lunch
python -m quant post_market_evening

# 日决策（决策卡 + 纸面撮合 + 推送）
python -m quant daily_decision
python -m scripts.decision.daily --no-push    # 仅落盘
python -m scripts.decision.daily --dry-run     # 不撮合，仅打印信号

# 预取概念/粘合度（05:00，可选）
python -m quant prefetch_concepts
```

加 `--no-push` 只落盘不推飞书。每次运行：拉取 API 数据 → 落盘 `~/.quant/daily/` → 按模式执行 → 推送飞书。

### 4.2 建议调度

与内置调度器默认一致（工作日，`QUANT_SCHEDULER_ENABLED=true`，`app/scheduling/quant_scheduler.py`）：

| 时间 | 模式 |
|------|------|
| 05:00 | `prefetch_concepts`（可选） |
| 8–22 每个整点 | `news` |
| 09:25 | `pre_market` |
| 09:37–15:00 每 7 分钟 | `during_market` |
| 11:50 | `post_market_lunch` |
| 16:00 | `maintain`（数据维护：无库建库 / 查漏补漏 / 当日增量） |
| 20:10 | `post_market_evening` |
| 20:45 | `daily_decision` |

时点可在 `.env` 覆盖（如 `GOLDQUANT_QUANT_SCHED_DURING_MARKET_TIMES`、`GOLDQUANT_QUANT_SCHED_MAINTAIN_DAILY_TIME`）。也可用 cron / 任务计划调用 `python -m quant <mode>`；工作目录为项目根并激活 venv。

**数据维护任务（16:00 `maintain`）**：`scripts/data/maintain.py` 自愈离线库——无库则全量 `build_daily`，有库则扫描交易日历缺口并用 `build_daily --ignore-existing` 回补，最后跑 `update_daily` 当日增量；拉取全程指数退避 + 限流加倍兜底（`quant/data/fetch.py:_retry`）。**首次建议手动** `python -m scripts.data.build_daily --start 2021-01-01 --workers 3`（夜间，数千只历史耗时数小时），之后 `maintain` 只做增量/补漏。手动补历史缺口：`python -m scripts.data.build_daily --start <起> --end <止> --ignore-existing`。

### 4.3 飞书推送（r3 六事件）

| 标签 | 模式 | 要点 |
|------|------|------|
| 新闻聚焦 | `news` | LLM 去噪要点 + 综合解读 |
| 盘前准备 | `pre_market` | 指数 / 纸面账户 / 关注 |
| 智能盯盘 | `during_market` | 指数 / 纸面持仓 / 异动 |
| 午间复盘 | `post_market_lunch` | 午前指数 + 纸面账户 |
| 收盘复盘 | `post_market_evening` | 收盘指数 + 纸面绩效 / 持仓 |
| 日决策·纸面成交 | `daily_decision` | 指令 / 成交 / 账户 / 持仓 |

格式：纯文本，`标题 + 时间 + 【小节】要点`（`quant/push/format.py`）。

### 4.4 历史回测

```powershell
python -m scripts.backtest.run --start 2026-06-17 --end 2026-06-26 --cash 100000
```

- **与实盘对齐**：默认 strict 口径，走同一 `TargetPortfolio` + `evaluate_exits` + `execute_signals`。
- **输出**：交易笔数、胜率、盈亏比、最大回撤、总回报、年化收益/波动、Sharpe/Sortino/Calmar、年化换手、出场归因（按 `Trade.reason` 分组）。
- 报告写 `$QUANT_HOME/reports/bt/`。

> 离线库由 16:00 `maintain` 自动维护（无库建库 / 查漏补漏 / 当日增量）；回测显著度取决于快照积累量，ML 校准要求 ≥100 样本，建议至少覆盖一轮趋势 + 一轮震荡再下结论。

---

## 五、配置

### 5.1 评分与组合参数

默认：`quant/config/quant.yml`（包内）；用户覆盖：`~/.quant/config/quant.yml`（deep merge）。组合参数见 §3.4，因子权重见 [R3_ARCHITECTURE.md §5](docs/R3_ARCHITECTURE.md#5-因子层l1指标含义与计算)。

### 5.2 硬门禁与撮合规则

`quant.yml` 的 `gates:` 段含：标的池、极端熔断、每日亏损限额、止损冷却、分档仓位上限、佣金/印花税/过户费/滑点、涨跌停、T+1 等（`quant/execution/sim_rules.py` 读取）。

`trading.time_validation_enabled: false` 时任意时刻可模拟成交（联调用）；实盘请改为 `true`。

---

## 六、ML 离线校准

ML **不参与盘中推理**，仅在收盘后（或周末）用历史数据优化阈值与维度权重。

### 6.1 数据来源

自动扫描 `~/.quant/daily/*/derived/` 评分，结合次日行情涨幅与后续成交盈亏构建标签。**至少积累约 100 条样本**后再跑（默认 `--min-samples 100`）。

### 6.2 命令

```powershell
python -m quant.ml calibrate --method grid --dry-run       # 预览
python -m quant.ml calibrate --method grid --apply         # 网格搜索阈值
python -m quant.ml calibrate --method linear --apply        # 线性回归 → 维度权重 + 阈值
python -m quant.ml calibrate --method lightgbm --apply      # LightGBM 特征重要性 → 权重
python -m quant.ml calibrate --method bayesian --apply      # 贝叶斯优化 → 阈值
```

### 6.3 生效方式

`--apply` 写入 `~/.quant/config/ml_calibration.yml`，下次 `python -m quant` 启动时自动合并到评分配置（优先级高于包内默认值）。取消 ML 覆盖：删除该文件或将 `apply: false`。

---

## 七、量化数据 API

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
    "指数": [],
    "赚钱效应": {},
    "自选股": [],
    "持仓股": []
  }
}
```

自选股 / 持仓从 `~/.quant/state/optional.jsonl`、`holding.jsonl` 读取并 enrich。`data/` 目录下为各接口样例 fixture。

### 其他行情 API

| 说明 | 路径 |
|------|------|
| 东财人气榜 | `/api/v1/hot/eastmoney/popularity` |
| 同花顺热榜 | `/api/v1/hot/ths` |
| 东财个股资讯 | `/api/v1/news/em?symbol=` |

完整列表见 <http://127.0.0.1:8085/docs>。

---

## 八、常见问题

**Q：quant 报连接失败？**
A：先确认 `python -m app` 已启动，且 `quant/config.py` 中 `BASE_URL` 与 API 端口一致（默认 `http://localhost:8085`）。或设 `QUANT_USE_LOCAL_FIXTURE=true` 读 `data/*.json`。

**Q：ML 提示样本不足？**
A：多运行若干交易日，确保每天晚间复盘产生 `daily/{date}/derived/` 评分。

**Q：`.env` 端口不生效？**
A：使用 `python -m app` 启动；裸 `uvicorn` 需显式 `--port`，见 `.env.example` 说明。

---

## 九、本地自检

```powershell
curl http://127.0.0.1:8085/health
curl http://127.0.0.1:8085/api/v1/quant/market/pre_market
python -m quant.ml calibrate --method grid --dry-run
python -m scripts.decision.daily --dry-run
```

---

## 许可证与致谢

- 项目代码以仓库许可为准。
- 数据版权归各提供方所有；感谢 [AKShare](https://github.com/akfamily/akshare)。
