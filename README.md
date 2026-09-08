# GoldQuant

A 股日频量化辅助系统：**FastAPI 数据聚合服务** + **动量双槽纸面交易** + **飞书推送**。

当前主策略（`quant.yml` → `momentum_swing`）：昨日收盘涨幅 Top6 + 沪深300 MA55 门控 + 双槽 T+1 开盘买 / 到期尾盘卖。`enabled: false` 时回退 IC 因子 + SwapGate + 盘中 θ 择时。

> 本仓库仅做数据聚合与纸面模拟交易辅助，**不构成投资建议**。行情来自 AKShare / 东财 / 同花顺等第三方，存在延迟、字段变更或访问失败的可能。

> 完整文档见 **[docs/README.md](docs/README.md)**（架构、因子、运维、配置、API）；路线图见 **[ROADMAP.md](ROADMAP.md)**。

---

## 怎么用

推荐顺序：**装环境 → 建历史库 → 回测看策略 → 盘后增量 + 晚间选股 → 次日盘中纸面**。  
不要一上来就 `python -m app`：没库的话调度跑了也选不出票。

下文示例数据根是 `D:\ProgramData\.quant`，请改成你 `.env` 里的 `GOLDQUANT_QUANT_HOME_DIR`。命令一律在项目根执行。

### 当前策略（默认已开）

配置：`quant/config/quant.yml` → `momentum_swing`（`enabled: true`）。

| 项 | 规则 |
|---|---|
| 股票池 | 非 ST、非北交所（代码非 4/8/9 开头）；20 日成交额 ≥ 8000 万；上市 ≥ 60 个交易日；**信号日未收盘涨停** |
| 选股 | 信号日 **T 收盘涨幅**截面 **Top6**（买昨天最强的） |
| 开仓门控 | T 日沪深300 收盘 **高于 MA55** 才允许 T+1 新开一槽；连续空仓满 **10 个交易日**则半槽强制开一次（避免长期空仓） |
| 买入 | **T+1 开盘**（再剔除开盘涨停）；纸面用盘中第一轮实时价近似开盘 |
| 卖出 | 买入后再过 **2 个收盘**（`hold_days=2`，约 T+3 收盘）；纸面在到期日 **14:30 之后**卖 |
| 仓位 | **双槽**，每槽 50% 净值；一天最多新开一个空槽；强制开仓时该槽 ×0.5（约 25% 净值） |
| 成本 | 佣金万一（最低 5 元、不免五）、卖出印花税 5bp、沪市过户 0.1bp |

关掉动量、回到旧 IC 因子 + SwapGate + 盘中 θ：把 `momentum_swing.enabled` 设为 `false`。

### 1. 安装

```powershell
cd D:\workspace\GoldQuant
poetry install
copy .env.example .env
```

`.env` **至少**填数据目录（飞书/LLM 可后配，只建库和回测不需要）：

```env
GOLDQUANT_QUANT_HOME_DIR=D:\ProgramData\.quant
```

飞书推送再填 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_USER_ID`。

### 2. 收集数据（离线日线库）

**第一次（必须）**：拉历史 K 线。全市场可能要很多小时，可中断后用同一命令续跑。

```powershell
# 单目录建库（简单；建库期间不要同时跑别的写库任务）
poetry run python -m scripts.data.build_daily --home D:\ProgramData\.quant --start 2021-01-01 --workers 1 --req-interval 5,10

# 补流通市值 / 昨收（选股过滤要用）
poetry run python -m scripts.data.backfill_daily_meta --home D:\ProgramData\.quant

# 校验，退出码 0 即可（允许 WARN）
poetry run python -m scripts.data.validate_library --home D:\ProgramData\.quant
```

库里至少要有：`daily_raw`（不复权 K）、`adj_factor`（复权）、`index_daily`（含沪深300）、`calendar`、`universe`。动量策略读后复权价 + 沪深300。

**每天盘后（必须）**：把「今天」写入正式库，否则当晚选股缺当日收盘。

```powershell
poetry run python -m scripts.data.update_daily --home D:\ProgramData\.quant
```

**每周（强烈建议）**：查缺补漏，避免静默少几天。

```powershell
poetry run python -m scripts.data.maintain --home D:\ProgramData\.quant
```

分流建库（历史目录 + 每日目录再 merge）见 [docs/OPERATIONS.md §3](docs/OPERATIONS.md#3-从零搭建离线库首次必须)。

### 3. 回测（与纸面同一套动量规则）

```powershell
poetry run python -m scripts.backtest.run_momentum --home D:\ProgramData\.quant --start 2021-01-01 --end 2026-08-14
```

| 你会看到 | 位置 |
|---|---|
| 年化 / 回撤 / 分年 | 终端 |
| 权益曲线 | `$QUANT_HOME/reports/bt_momentum/curve.csv` |
| 月度表 | `.../monthly.txt` |
| 完整报告 | `.../report.json` |

参数默认读 `momentum_swing`；可用 `--topn` / `--ma` / `--hold` / `--max-idle` 覆盖。

旧 IC 组合回测（仅 `enabled: false` 时有意义）：`poetry run python -m scripts.backtest.run --home ... --start ... --end ...`。口径与当前纸面不同，见 [OPERATIONS.md §5](docs/OPERATIONS.md#5-回测库就绪后研究时建议跑)。

### 4. 日内纸面模拟

纸面账户在 `$QUANT_HOME/paper_account/`，与人工 `state/` 隔离。流程是 **T 晚只写计划，T+1 盘中才成交**。

**当天晚上（选股，写入明日作战池）**

```powershell
poetry run python -m scripts.decision.daily --home D:\ProgramData\.quant
# 不推飞书：加 --no-push
# 不写纸面、只看选了谁：加 --no-paper --no-push
```

产出：

- `paper_account/battle_pool/{T+1}.json` — 次日要买的篮子  
- `paper_account/sell_watch/{T+1}.json` — 次日要监控卖出的持仓  
- `paper_account/state/mom_slots.json` — 双槽状态  
- `reports/decision/decision_{T}.txt` — 门控开/关、空仓天数、候选名单  
- 飞书「晚间复盘」（配了密钥才会发）

**次日盘中（真正买卖）**

```powershell
poetry run python -m quant during_market
```

- 先卖：到期持仓在 **14:30 后**卖；未到期不卖  
- 再买：读当天作战池，跳过开盘涨停，按槽位等权买（**不等**盘中 θ）  
- 飞书「智能盯盘」带成交摘要  

盘中需要实时行情，必须在交易时段跑。只跑一次也能成交（09:37 左右买、14:31 以后卖）；要贴近调度就按 `quant.yml` 的 `during_market_times` 多跑几次。

**第一次纸面**：先有库 → 交易日晚上跑 `daily` → **第二天交易时段**再跑 `during_market`。当天白天没有昨晚的作战池，买不进去。

### 5. 无人值守（调度）

数据和飞书都配好之后：

```powershell
poetry run python -m app
```

默认会自动跑：18:00 增量、20:10 选股、次日 09:37–14:59 盘中买卖。不必先开 API 才能手动 `quant during_market`。

| 时间 | 做什么 |
|---|---|
| 18:00 | `update_daily` 当日 K 线入库 |
| 20:10 | `daily_decision` 选股、写池、飞书 |
| 09:37 起约每 7 分钟 | `during_market` 纸面买卖、飞书 |
| 周五 22:00 | `maintain` 补缺 |

细项：[docs/DAILY_OPS.md](docs/DAILY_OPS.md)、[docs/OPERATIONS.md §7](docs/OPERATIONS.md#7-启动定时任务要无人值守则必须)。

---

## 架构概览

```text
┌─────────────────────────────────────────────────────────────┐
│  common/  公共基础设施（最底层；只依赖标准库/三方）             │
│           config(Settings) / timeutil / progress_log / utils │
└───────────────────────────┬─────────────────────────────────┘
                            ▲
          ┌─────────────────┴─────────────────┐
          │                                    │
┌──────────▼──────────────┐   ┌───────────────▼──────────────────┐
│  app/   数据 API（FastAPI，│   │  quant/  决策与运维               │
│  默认 :8085）五时段聚合接口 │   │  （poetry run python -m quant）    │
│  + 调度器（APScheduler）   │   │  L0→L1→L2→L3 纸面撮合→飞书推送    │
└───────────────────────────┘   │  L4 时序出场（优先于再平衡）       │
                                └───────────────┬──────────────────┘
                                                │ 离线
                                ┌───────────────▼──────────────────┐
                                │  research/  walk-forward / DSR    │
                                │  / 敏感性（参数治理）              │
                                └──────────────────────────────────┘
```

**依赖方向（硬约束）**：`common ← quant ← {app, scripts}`。`quant/` 不 import `app/`（r3 前经 HTTP、r3 起直调 service，均不经 app 包）；`common/` 不依赖 app/quant。运维五时段（news/pre/during 等）由 `quant/services/market/payload` 直调 service 构建 payload（不经 HTTP）；`daily_decision` 用离线库。**单次 `poetry run python -m quant <mode>` 无需启动 app**；仅内置调度器自动跑五时段时需 `poetry run python -m app`（调度器在 app 进程内）。

**职责划分**

| 组件 | 做什么 | 不做什么 |
|------|--------|----------|
| 动量策略 + 纸面撮合 | 决定买卖、仓位（双槽、开盘买、到期卖） | — |
| IC 因子 + 目标组合 | `momentum_swing.enabled: false` 时的回退路径 | — |
| LLM | 解读数据、写新闻文案 | 不参与下单决策 |

设计原则：**可回溯优先**（主链路只用 ≤T 信息重建）、**相对优于绝对**（横截面排名而非绝对分数）。动量路径是截面 continuation + 大盘均线门控；IC 回退路径才是「目标组合差额 + 盘中择时」。

---

## 项目结构

```text
GoldQuant/
├── common/                    # 公共基础设施层（最底；config/timeutil/progress_log/utils）
├── app/                        # FastAPI 数据服务
│   ├── main.py
│   └── api/v1/endpoints/
│       ├── quant_endpoint.py   # 五时段聚合接口（自选/持仓 enrich）
│       └── mkt_*.py             # 行情/热度/资金流接口
├── quant/                      # r3 量化核心
│   ├── data/                   # L0 离线库、日历、复权、宇宙、行业 PIT
│   ├── store/                  # state/paths/snapshot/views + 自选/观察池
│   ├── factors/                # L1 因子库、中性化、合成 alpha、面板
│   ├── portfolio/             # L2 目标组合（buffer/约束/vol target）
│   ├── execution/              # L3 成本规则、滑点、撮合器
│   ├── decision/               # 日决策卡 + 纸面撮合入口
│   ├── swing/                  # 动量策略（momentum.py / momentum_bt.py）+ 波段研究
│   ├── backtest/              # L3 回测引擎（broker/metrics/report）
│   ├── exit/                   # L4 时序出场（ATR/硬止损/趋势/时间）
│   ├── ops/                    # 日运维：新闻/盘前/盯盘/复盘 + 推送
│   ├── push/                   # 飞书推送
│   ├── journal/                # 漏斗统计 + 偏离日志（辅助决策闭环）
│   ├── research/              # walk-forward / DSR / 敏感性（参数治理）
│   ├── market/                 # 资金流 + 市场状态（regime）
│   ├── data/quote.py           # 盘口/历史行情纯价工具（从 scoring 迁出）
│   ├── pool/                   # 候选池（被 factors/app 引）
│   ├── signals/{models,sell_policy}  # 信号模型 + 卖出时段门（撮合活依赖）
│   ├── narrative/{llm,prompts} # LLM 叙述（仅 news 摘要，运维推送活依赖）
│   └── config.py / timeutil.py / trading_hours.py / data_fetch.py
├── scripts/{decision,backtest,data,factors,research}/
├── docs/                       # 文档（见 docs/README.md）
├── pyproject.toml              # 依赖真源（Poetry / PEP 621）
└── poetry.lock
```

**运行时数据目录**（自动创建）：`~/.quant/`，纸面账户在 `~/.quant/paper_account/`。报告在 `~/.quant/reports/`。

可通过环境变量覆盖存储位置（优先级从高到低）：

1. `GOLDQUANT_QUANT_HOME_DIR`（写进 `.env`，与其它 `GOLDQUANT_` 前缀一致）；
2. `QUANT_HOME`（shell 临时覆盖，无需前缀）；
3. 默认 `~/.quant`。

```text
~/.quant/
├── store/          # Parquet 日线库（daily_raw / adj / index_daily / universe …）
├── state/          # 人工 holding.jsonl、account.json
├── views/          # holding.md（自动生成，勿手改）
├── daily/{date}/   # raw/ derived/ trades/ review/
├── config/         # 用户覆盖 quant.yml
├── paper_account/  # 纸面账户（与人工仓隔离）
│   ├── battle_pool/
│   ├── sell_watch/
│   └── state/      # holding.jsonl + mom_slots.json
├── reports/        # 决策 / 回测
└── memory/         # 新闻摘要
```

---

## 环境要求

- Python **3.11+**（推荐 3.11）
- 可访问外网（拉取行情）
- Windows / Linux 均可

---

## 一、安装

在项目根目录 `GoldQuant` 下（需已安装 [Poetry](https://python-poetry.org/) 2.x）：

```powershell
# 安装主依赖（自动使用/创建项目 .venv）
poetry install

# 需要 ML 离线校准（IC/拟合等）时
poetry install --extras ml
```

复制环境变量模板：

```powershell
copy .env.example .env
```

编辑 `.env`，至少配置：

| 变量 | 说明 |
|------|------|
| `GOLDQUANT_QUANT_HOME_DIR` | 数据根目录（如 `D:\ProgramData\.quant`）；也可用 `QUANT_HOME` |
| `GOLDQUANT_PORT` | API 端口，默认 `8085` |
| `LLM_API_KEY` | LLM 密钥（复盘叙述） |
| `LLM_BASE_URL` | LLM 接口地址 |
| `LLM_MODEL` | 模型名 |
| `FEISHU_APP_ID` | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 飞书应用密钥 |
| `FEISHU_USER_ID` | 飞书接收人 open_id |

> 下文命令统一用 `poetry run python -m ...`，避免误用系统 Python。详细运维见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

---

## 二、启动数据 API（调度 / Swagger）

库已建好、要**无人值守纸面**时再开。单次选股或盘中模拟用上一节命令即可，**不必**先启动 API。

```powershell
# 推荐
poetry run python -m app

# 或（需自行带 host/port）
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8085
```

- 文档：<http://127.0.0.1:8085/docs>
- 健康检查：<http://127.0.0.1:8085/health>

Linux 后台常驻：

```bash
chmod +x run.sh
./run.sh start
```

> **注意**：`poetry run python -m quant <mode>` 直调 service 构建 payload（不经 HTTP），单次运行无需先启动 API。仅当用内置调度器自动跑五时段时才需 `poetry run python -m app`（调度器在 app 进程内）。`QUANT_USE_LOCAL_FIXTURE=true` 时改读 `data/*.json` fixture，连 service 也不调。

---

## 三、业务逻辑

默认 **动量双槽**（`scripts/decision/daily.py` + `quant/swing/momentum.py`）：T 晚定计划，T+1 开盘买、到期尾盘卖。LLM 只负责新闻解读，推送正文由模板生成。

### 3.1 动量策略（当前主路径）

```text
T 晚 daily_decision
  流动池（非 ST / 非北交 / ADV20≥8000 万 / 上市≥60 日 / 未收盘涨停）
  → 昨日收盘涨幅 Top6
  → 沪深300 收盘 > MA55 才开新槽；连续空仓满 10 日则半槽强制
  → write_battle_pool(T+1) + sell_watch（到期尾盘卖）
  → 飞书「晚间复盘」

T+1 盘中 during_market（约每 7 分钟，先卖后买）
  14:30 后：到期持仓 force_sell（近似收盘）
  开盘附近：按作战池排名买入，跳过开盘涨停（不等盘中 θ）
  双槽各 50%；一天最多填一个空槽
```

- 配置：`quant.yml` → `momentum_swing`（`enabled` / `topn` / `ma` / `hold_days` / `max_idle`）。
- 槽位状态：`paper_account/state/mom_slots.json`。
- `enabled: false` 时回退下面的 IC + 盘中 θ 路径。
- 官方回测与纸面同规则：`poetry run python -m scripts.backtest.run_momentum`。

### 3.1b IC 回退路径（`momentum_swing.enabled: false`）

```text
T 晚：compose_alpha → 作战池 + evaluate_exits → sell_watch
T+1 盘中：compose_intraday_alpha，α_z ≥ 1.0 才买
```

日频 20 因子 + 5 个盘中因子见 [FACTORS.md](docs/FACTORS.md)。目标组合 / SwapGate / L4 出场仅在此回退路径使用。

### 3.2 五时段运维推送（`quant/ops/`）

| 模式 | 命令 | 默认调度 | 推送正文 |
|------|------|-----------|----------|
| 新闻 | `news` | 8–22 每个整点 | LLM |
| 盘前 | `pre_market` | 09:25 | 模板（指数+纸面账户+关注） |
| 盘中 | `during_market` | 09:37 起每 7 分钟（至 15:00） | 模板（指数+纸面持仓+异动）+ **动量开盘买 / 到期卖** |
| 午间复盘 | `post_market_lunch` | 11:50 | 模板（午前指数+纸面账户） |
| 收盘复盘 | `post_market_evening` | 20:10 | 模板（收盘指数+纸面绩效+持仓） |

- `news` 走 LLM（`prompt_news`）去噪要点 + 综合解读，摘要落 `~/.quant/memory/` 供盘前引用。
- 其余四时段由 `ops/modes.py` 模板生成（指数 + 纸面账户/持仓 + 涨幅榜），**不改自选**；其中 `during_market` 会执行纸面买卖（见 §3.1）。
- `daily_decision` 推送「明日作战池 + 卖出监控」（T 晚只定计划；买卖在 T+1 `during_market`）。

### 3.3 因子层（L1，IC 回退用）

因子在**后复权**序列上计算，仅用 `≤ as_of` 数据。流水线：

```text
raw × direction → winsorize(1%,99%) → 行业+log市值中性 → z-score → Σ w_i z_i = alpha
```

`compose_alpha`（`quant/factors/compose.py`）= 加权 z-score 均值。20 个日频因子 + 5 个盘中因子的含义、公式与权重见 [FACTORS.md](docs/FACTORS.md)。

#### 3.3b 盘中因子层（择时，`quant/factors/library/intraday.py`）

`compose_intraday_alpha`（`quant/factors/compose.py`）对作战池用 5 个**盘中因子**（基于 `fetch_spot_em` 实时快照字段）截面 z-score 加权合成 `intraday_alpha`（α_z），用于 T+1 盘中择时触发：

| 因子 | 计算 | 默认权重 |
|------|------|-----:|
| `intraday_strength` | (最新−今开)/今开（开盘后走强） | 1.0 |
| `volume_ratio` | 量比（放量） | 1.0 |
| `speed` | 涨速（盘中加速） | 0.8 |
| `day_change` | 涨跌幅 | 0.6 |
| `turnover` | 换手率 | 0.5 |

触发：IC 回退路径作战池内 `α_z ≥ 1.0`。动量主路径不使用该阈值。

### 3.4 目标组合（L2，`quant/portfolio/target.py`）

> **注意**：动量主路径不走本节。仅当 `momentum_swing.enabled: false` 时，目标组合用于 IC 回测 strict 口径与回退纸面。

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

排名 buffer：`n_enter < rank ≤ n_exit` 的已持仓不因排名抖动频繁进出。完整说明见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)。

### 3.5 时序出场（L4，`quant/exit/rules.py`）

出场检查**优先于**再平衡。`evaluate_exits` 并行算四类信号，按优先级取一个：`hard_stop > atr_trailing > trend_stop_ma20 > time_stop`。

| 规则 | reason | 触发条件 |
|------|--------|----------|
| 硬止损 | `hard_stop` | `C ≤ max(入场×0.92, 入场−2×ATR14)`（取较紧者） |
| ATR 跟踪 | `atr_trailing` | `C ≤ max(入场价, 持仓最高收盘) − 3×ATR14` |
| 趋势止损 | `trend_stop_ma20` | 收盘连续 2 日 < MA20 |
| 时间止损 | `time_stop` | 持有 > 20 交易日**且浮盈 < 0** |
| 排名出场 | 经 L2 | 排名 > `n_exit` → 目标权重 0 |

完整说明见 [ARCHITECTURE.md §7](docs/ARCHITECTURE.md#7-l4-出场规则)。

### 3.6 纸面撮合（L3，`quant/execution/executor.py` + `decision/paper_execute.py`）

`execute_signals` 两阶段，**先卖后买**：

- A 股 T+1（当日买入不可卖）、当日已卖不回补；
- 涨跌停封板拒单；100 股整数倍；可用资金不足跳过买入；
- 佣金万一（最低 5 元）、卖出印花税、沪市过户费、滑点；
- 整手股数：`⌊amount/price/100⌋×100`；单日成交额 ≤ 当日 `amount×5%`（ADV）。

纸面账户隔离：`paper_home_context` 把 state 根切到 `{quant_home}/paper_account/`，与人工主账户互不污染。动量路径买卖走 `execute_momentum_buys` / `sell_watch`；IC 回退仍与回测共享 `TargetPortfolio` / `evaluate_exits`。

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
poetry run python -m quant news
poetry run python -m quant pre_market
poetry run python -m quant during_market
poetry run python -m quant post_market_lunch
poetry run python -m quant post_market_evening

# 日决策（作战池 + 卖出监控 + 飞书）
poetry run python -m quant daily_decision
poetry run python -m scripts.decision.daily --no-push    # 仍写纸面文件，不推飞书
poetry run python -m scripts.decision.daily --dry-run     # 同上（不推飞书）
poetry run python -m scripts.decision.daily --no-paper    # 只出报告，不写纸面账户

# 预取概念/粘合度（05:00，可选）
poetry run python -m quant prefetch_concepts
```

加 `--no-push` 只落盘不推飞书。每次运行：直调 service → 落盘 `~/.quant/daily/` → 按模式执行 → 推送飞书。

### 4.2 建议调度

与内置调度器默认一致（工作日，`scheduler.enabled=true`，见 `quant/config/quant.yml` → `app/scheduling/quant_scheduler.py`）：

| 时间 | 模式 |
|------|------|
| 05:00 | `prefetch_concepts`（可选） |
| 8–22 每个整点 | `news` |
| 09:25 | `pre_market` |
| 09:37–15:00 每 7 分钟 | `during_market` |
| 11:50 | `post_market_lunch` |
| 18:00 | `update_daily`（盘后增量：spot_em + 快照，分钟级） |
| 周五 22:00 | `maintain`（每周自愈：建库 / 补漏 / retry-failed） |
| 20:10 | `daily_decision`（含原 post_market_evening 职责） |

时点请在 `quant/config/quant.yml` 或 `~/.quant/config/quant.yml` 的 `scheduler` 段修改（如 `during_market_times`、`update_daily_time`、`maintain_weekly_time`）。也可用 cron / 任务计划调用 `.venv` 中的解释器，例如 `.venv\Scripts\python.exe -m quant <mode>`（工作目录为项目根）。

**盘后增量（每日 18:00 `update_daily`）**：一次 `spot_em` 全市场 + 指数/行业/universe/因子快照，分钟级，只补当天。

**离线库与每日增量合并**：全量 `build_daily --end <昨日>` 与每日 `update_daily` 分 home 跑，build 完成后 `scripts.data.merge_library --offline <离线home> --daily <增量home> --out <统一home>` 合并（daily_raw 归一 13 列、update 覆盖 build、复权沿用 raw+factor 分离，不复发除权尖刺）；`scripts.data.backfill_daily_meta --home <统一home>` 用 `stock_value_em` 补历史段 `float_mv/total_mv/pre_close`（精确市值；默认只拉仍缺市值的码，边拉边落盘可续传，`--force` 全量重拉；name 不灌历史避免 ST 前视）；`scripts.data.validate_library --home <统一home>` 一键校验完整性与正确性（含复权连续性 + 市值覆盖）。完整**执行步骤**见 [docs/OPERATIONS.md §5.0](docs/OPERATIONS.md#50-执行步骤总览从零到可回测)。

**离线库自愈（每周五 22:00 `maintain`）**：`scripts/data/maintain.py` 自愈离线库——无库则全量 `build_daily`，有库则扫描交易日历缺口并用 `build_daily --ignore-existing` 回补，最后跑 `update_daily` 当日增量 + `build_daily --retry-failed` 重试失败码；拉取全程指数退避 + 限流加倍兜底（`quant/data/fetch.py:_retry`）。重活拆到周五晚，避免日常 `update_daily` 被长 `build_daily` 阻塞。**首次建议手动** `poetry run python -m scripts.data.build_daily --start 2021-01-01 --workers 1 --req-interval 5,10`（夜间；中断后重复同一命令即可智能续传：未完成代码 + 市场级漏日）。参数详见 [docs/OPERATIONS.md](docs/OPERATIONS.md)。

### 4.3 飞书推送（r3 六事件）

| 标签 | 模式 | 要点 |
|------|------|------|
| 新闻聚焦 | `news` | LLM 去噪要点 + 综合解读 |
| 开盘啦 | `pre_market` | 指数 / 纸面账户 / 关注 |
| 智能盯盘 | `during_market` | 指数 / 纸面持仓 / 异动 / 盘中成交 |
| 午间复盘 | `post_market_lunch` | 午前指数 + 纸面账户 |
| 收盘复盘 | `post_market_evening` | 收盘指数 + 纸面绩效 / 持仓 |
| 晚间复盘 | `daily_decision` | 作战池 / 卖出监控 / 账户 / 持仓 |

格式：纯文本，`标题 + 时间 + 【小节】要点`（`quant/push/format.py`）。

### 4.4 历史回测

当前主策略（与纸面同规则）：

```powershell
poetry run python -m scripts.backtest.run_momentum --home D:\ProgramData\.quant
```

报告在 `$QUANT_HOME/reports/bt_momentum/`（`report.json` / `curve.csv` / `monthly.txt`）。

IC / SwapGate 回测（`momentum_swing.enabled: false` 时的组合路径）：

```powershell
poetry run python -m scripts.backtest.run --start 2026-06-17 --end 2026-06-26 --max-positions 10
```

- IC 回测默认 strict 口径：`TargetPortfolio` + `evaluate_exits` + `execute_signals`。
- 报告写 `$QUANT_HOME/reports/bt/`。

> 离线库由每周五 22:00 `maintain` 自动自愈（无库建库 / 查漏补漏 / retry-failed），日常 18:00 只跑 `update_daily` 当日增量（分钟级）；回测显著度取决于快照积累量，ML 校准要求 ≥100 样本，建议至少覆盖一轮趋势 + 一轮震荡再下结论。

---

## 五、配置

### 5.1 策略与组合参数

默认：`quant/config/quant.yml`（包内）；用户覆盖：`~/.quant/config/quant.yml`（deep merge）。主策略段为 `momentum_swing`。环境/敏感项在 `.env`。**所有配置项及说明见 [docs/CONFIG.md](docs/CONFIG.md)。**

### 5.2 硬门禁与撮合规则

`quant.yml` 的 `gates:` 段含：标的池、极端熔断、日内亏损限额、止损冷却、佣金/印花税/过户费/滑点、T+1 等（`sim_rules.py` / `risk_gate.py` 读取）。涨跌停幅度由代码按板块/ST 自动判定，不在 yml 配置。完整键说明见 [CONFIG.md](docs/CONFIG.md)。

`trading.time_validation_enabled: false` 时任意时刻可模拟成交（联调用）；实盘请改为 `true`。

---

## 六、ML 离线校准（已退役）

r3 决策链改用 IC 驱动权重（`~/.quant/config/factor_weights.yml`，由 `scripts/factors/fit_weights.py` 拟合）；r1 的评分阈值/维度权重校准（`quant.ml calibrate`）随评分体系退役。因子 IC 检验见 `scripts/factors/ic_report.py`。

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

持仓从 `~/.quant/state/holding.jsonl` 读取并 enrich（r1 自选池 `optional.jsonl` 已退役）。`data/` 目录下为各接口样例 fixture。

### 其他行情 API

| 说明 | 路径 |
|------|------|
| 东财人气榜 | `/api/v1/hot/eastmoney/popularity` |
| 同花顺热榜 | `/api/v1/hot/ths` |
| 东财个股资讯 | `/api/v1/news/em?symbol=` |

完整列表见 <http://127.0.0.1:8085/docs>。

---

## 八、常见问题

**Q：盘中 `during_market` 没有买入？**  
A：先看昨晚有没有写出 `paper_account/battle_pool/{今天}.json`。门控关且空仓未满 10 日时作战池为空，这是策略行为。非交易时段也买不成。

**Q：quant 运行报错 / 缺 pandas？**
A：请用 `poetry run python -m ...`，不要直接用系统 `python`。CLI 直调 service（不经 HTTP），无需先启动 API。缺依赖执行 `poetry install`（ML 加 `--extras ml`）。离线验证可设 `QUANT_USE_LOCAL_FIXTURE=true` 读 `data/*.json`。

**Q：ML 提示样本不足？**
A：多运行若干交易日，确保每天晚间复盘产生 `daily/{date}/derived/` 评分。

**Q：`.env` 端口不生效？**
A：使用 `poetry run python -m app` 启动；裸 `uvicorn` 需显式 `--port`，见 `.env.example` 说明。

---

## 九、本地自检

```powershell
curl http://127.0.0.1:8085/health
curl http://127.0.0.1:8085/api/v1/quant/market/pre_market
poetry run python -m scripts.decision.daily --dry-run
```

---

## 许可证与致谢

- 项目代码以仓库许可为准。
- 数据版权归各提供方所有；感谢 [AKShare](https://github.com/akfamily/akshare)。
