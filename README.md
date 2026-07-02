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
│   ├── config/                 # quant.yml（含 scoring + gates）/ industry_aliases.yml
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
├── config/         # quant.yml（含 gates 段）、ml_calibration.yml（用户覆盖）
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

## 三、业务逻辑（五时段）

系统按 **五种模式** 定时或手动运行。原则：**买卖、加自选、仓位均由规则引擎决定**；LLM 只负责解读与叙述，盘中推送正文甚至由模板直接生成。

```text
                    ┌─────────────┐
  news ────────────►│ LLM 解读    │──► 新闻摘要 → 全球宏观维度
                    └─────────────┘

  pre_market ──────► 评分/买信号（仅落盘） + LLM 盘前文案
                    （不计三确认、不成交、不改自选）

  during_market ───► 评分 → 买卖信号 → 三确认 → 模拟成交
                    + 模板化飞书推送（不改自选）

  post_market_lunch ► LLM 上午复盘（不改自选、不交易）

  post_market_evening ► 候选池评分 → 更新自选/观察池
                      + LLM 复盘 + 确定性「自选更新」段
```

### 3.1 各时段做什么

| 模式 | 命令 | 默认调度* | 改自选 | 买卖 | 推送正文 |
|------|------|-----------|:------:|:----:|----------|
| 新闻 | `news` | 07–23 部分整点（避开盘中/午间） | — | — | LLM |
| 盘前 | `pre_market` | 09:25 | 否 | 信号落盘，**不成交** | LLM |
| 盘中 | `during_market` | **09:37 起每 7 分钟**（至 15:00） | 否 | **可成交** | **模板**（非 LLM） |
| 午间复盘 | `post_market_lunch` | 11:50 | 否 | 否 | LLM |
| 晚间复盘 | `post_market_evening` | 20:10 | **是** | 否 | LLM + 自选段 |

\* 调度由 `app/scheduling/quant_scheduler.py` 驱动，时点可在 `.env` 中覆盖（如 `GOLDQUANT_QUANT_SCHED_DURING_MARKET_TIMES`）。  
另：**05:00** 预取自选/持仓的 **概念与粘合度**（周缓存，减轻 enrich 耗时）。

#### 新闻（`news`）

1. 拉取财经新闻列表，LLM 生成解读文案并推送飞书。  
2. 从文案中提取「综合解读」写入 `~/.quant/memory/`，并刷新 **全球宏观** 评分（影响后续各时段 `global_macro` 维度，权重 2）。  
3. **不参与** 自选、买卖、候选池。

#### 盘前（`pre_market`）

1. API 聚合：指数、赚钱效应、**当前自选/持仓**（enrich 后）、板块榜等。  
2. 对自选股扫描 **买入原始信号**（见下文「买入逻辑」），写入 `derived/signals.json`。  
3. **持续确认不计数**（配置 `confirmation.skip_count_modes` 含 `pre_market`），**不产生可执行单、不模拟成交**。  
4. LLM 根据 `engine_brief`（程序摘要）写开盘分析；「操作」段仅展示引擎结论，**不执行**。

#### 盘中（`during_market` / 智能盯盘）

1. 同上拉数，对 **自选股** 生成买入信号、对 **持仓** 生成卖出信号。  
2. 经 **同日防翻转**（当日已卖不买、已买不卖）与 **持续确认** 后，可执行信号交给模拟成交器。  
3. 持仓股单独评分，落盘 `scores_holding.json`。  
4. 推送正文由 `build_during_market_push` **模板生成**（大盘、概念榜、自选异动、持仓、信号与成交、三确认进度等）。  
5. **不修改自选列表**；自选变更仅发生在晚间复盘。

#### 午间复盘（`post_market_lunch`）

1. 上午行情与自选/持仓表现摘要 + LLM 叙述。  
2. 不生成买卖信号，不更新自选。

#### 晚间复盘（`post_market_evening`）

1. 用当日四榜更新 **概念板块快照**（`concept_tracker.json`，供次日盘中/评分使用）。  
2. **候选池评分 → 自选池更新**（见下文「加自选逻辑」）。  
3. LLM 写全天复盘；文末 **追加确定性段落「六、自选更新」**（新增、移观察池、观察池恢复等，不由 LLM 编造）。

---

### 3.2 评分引擎（100 分制）

对单只股票按 **启用维度加权平均** 得到总分（各维度 0–100，缺失则不计入分母）：

| 维度 | 默认权重 | 说明 |
|------|---------:|------|
| `main_wave` | **35** | 主升浪加速段形态、震荡剔除、买点结构（趋势核心，集中奖励） |
| `stock_fund_flow` | 11 | 个股资金流（流出可负分）。**数据源按模式**：智能盯盘始终用 `个股资金流` 的 `大单流入−大单流出`；晚间复盘优先取 `个股资金流日线` 最新一条，若为当天则用其 `主力净流入-净额`，否则回退 `大单流入−大单流出`。原始值带「万/亿」单位已换算为元。**全口径统一**（评分/买入门禁/推送展示/资金快照均同源，见 `quant.market.fund_flow`） |
| `concept_theme` | 9 | 概念/行业与板块榜 **共振**；概念优先 **同花顺 F10 粘合度** 加权 |
| `stock_history` | **8** | 近 30 日大涨占比、均线发散、周/月线（与 main_wave 重叠，已下调） |
| `day_bar_shape` | 8 | 收阴、冲高回落（晚间候选加重收阴惩罚） |
| `popularity_rank` | **6** | 同花顺人气榜排名 |
| `technical` | **5** | MACD、均线等（与 main_wave MA 重叠，已下调） |
| `market_sentiment` | 4 | 涨跌家数、涨停家数 |
| `ths_rank_signal` | **2** | 形态榜标签（创新高/量价齐升等，已下调） |
| `market_fund_flow` | 2 | 大盘资金流 |
| `market_index` | 3 | 大盘指数涨跌 |
| `zt_height` | 3 | 连板高度 |
| `global_macro` | 2 | 新闻解读后的宏观多空 |

**阈值**（`quant/config/quant.yml`，可被 `~/.quant/config/quant.yml` 覆盖）：

| 阈值 | 默认 | 用途 |
|------|-----:|------|
| `watchlist_entry_threshold` | **72** | 晚间复盘 **新进自选 / 观察池恢复**（hysteresis 上沿）|
| `watchlist_exit_threshold` | **66** | 连续未达标 **移观察池**（hysteresis 下沿）|
| `watchlist_threshold` | 70 | 参考中位（ML 校准目标 / engine floor 引用）|
| `buy_threshold` | **72** | 盘中/盘前 **买入信号** 最低分 |
| `sell_threshold` | 45 | 已关闭「评分走弱卖出」；保留配置项 |
| `watchlist_require_main_wave` | **true** | **B 硬门禁**：加自选强制 `主升波段=True`（非主升浪票即便总分达标也不进自选）；`false`=仅靠 main_wave 高权重软抑制（A） |

**推送展示过滤**（`push`，仅影响推送展示，**不**影响加自选/买卖/评分/落盘）：

| 配置 | 默认 | 用途 |
|------|-----:|------|
| `push.watchlist_price_range.enabled` | true | 启用「自选异动 / 自选更新」价格区间过滤 |
| `push.watchlist_price_range.min` | 5 | 现价下限（元，含；null=无下限）|
| `push.watchlist_price_range.max` | 150 | 现价上限（元，含；null=无上限）|
| `push.watchlist_score_filter.enabled` | true | 启用晚间「自选更新」当天评分过滤 |
| `push.watchlist_score_filter.min_score` | 70 | 当天评分下限；未配置时取 `scoring.watchlist_threshold` |

> 启用后，盘中「自选异动」与晚间「自选更新」**仅推送**现价落在 `[min, max]` 的标的；晚间「自选更新」另过滤当天评分 `< min_score` 的标的（hysteresis 死区保留但不再推送）。加自选、买卖、评分、落盘均不受限；价格/评分缺失的标的保留不误删；移入观察池 / 观察池期满删除为变更日志，不受此过滤影响。

> **自选 hysteresis（消除单点悬崖）**：进自选要 ≥72、清退要 <66，**[66,72) 为死区**——已在自选的票只要不低于 66 就保留、不计未达标；新票低于 72 不进。避免分数在 70 附近抖动导致自选来回 churn。`watchlist_threshold=70` 仅供 ML 校准与 engine floor 使用。

**概念维度要点：**

- enrich 时 **优先** 拉取同花顺 F10 **概念粘合度**，失败再问财；缓存 **7 天**（`~/.quant/cache/stock_concepts.json`）。  
- 评分时按粘合度 rank 加权与板块榜共振，**不再** 在全部概念里只取窗口最优一个。  
- 推送展示个股概念时，优先 **粘合度第 1、2、3** 名（无粘合度则取所属概念前 3）。

---

### 3.3 加自选逻辑（仅晚间复盘）

```text
人气榜 + 同花顺形态榜 (+ 可选涨停池)
        ↓ API 初筛 + enrich（行情/概念/资金流…）
        ↓ build_candidates 合并候选
        ↓ ScoringEngine 全量评分
        ↓ 总分 ≥ watchlist_entry_threshold(72) → passed_rows
        ↓ merge_watchlist_evening：合并历史自选（存量总是保留）
        ↓ 保留自选若未进候选池 → 补算评分
        ↓ 连续 N 日 < watchlist_exit_threshold(66) → 移入观察池（默认 N=3）
        ↓ 观察池内继续 nightly 评分；≥ entry(72) → 恢复自选
        ↓ 观察超过 M 日仍不达标 → 删除（默认 M=30）
        ↓ save_optional / save_observe → 推送「自选更新」（按评分降序，可配置价格区间过滤）
```

- **候选来源**：默认 **人气榜前 20** + **形态榜**（创月/半年/年/历史新高、持续上涨、持续放量、量价齐升）；涨停池默认 **不进入** 候选。  
- **观察池**：不参与买入扫描、不出现在 API「自选股」、不推送给 LLM 当可操作标的。  
- **加入原因** 人类可读单行，含粘合度 Top3 概念、形态标签、人气排名、总分等。
- **去重**：合并历史自选 / 观察池恢复时按代码去重，避免 `optional.jsonl` 重复行在「自选更新」「自选异动」重复展示。
- **价格区间过滤**（可选）：`push.watchlist_price_range.enabled=true` 时，盘中「自选异动」与晚间「自选更新」仅推送现价落在 `[min, max]` 区间的标的；**加自选 / 买卖 / 评分 / 落盘均不受限**，价格未知（enrich 缺失）的标的保留不误删。
- **评分过滤**（可选）：`push.watchlist_score_filter.enabled=true` 时，晚间「自选更新」仅推送当天评分 ≥ `min_score`（默认 `scoring.watchlist_threshold=70`）的标的；hysteresis 死区 `[66,72)` 保留但不再推送。

---

### 3.4 买入逻辑（盘前信号 / 盘中可成交）

**标的范围**：仅 **自选股**（不含观察池、不含仅候选未入选）。  
**策略**：仅 **主升浪战法**（加速段买入 / 回调企稳买入）。

须 **全部通过**（顺序简化）：

1. **硬门禁** `check_buy_gates`：标的池、熔断、仓位上限、ST 等（见 `quant.yml` 的 `gates` 段）。  
2. **趋势** `trend_allows_buy`：须处于允许做多的趋势阶段。  
3. **动能** `momentum_score` ≥ 配置下限。  
4. **总分** ≥ `buy_threshold`（72，可按市场档位动态调整）。  
5. **买点** `detect_buy_setup`：加速段或回调企稳结构成立。  
6. **涨幅上限**：追高过滤（加速/回调类型可不同上限）。  
7. **盘中额外**：`intraday_allows_buy` 分时确认（盘前只生成信号，不做此项拦截落盘）。

通过者按总分排序（动能分有约 20% 加成参与排序键），在 **剩余仓位空位** 内按分数分配数量。

**持续确认（仅盘中计入）**：

- 首次触发当日 **锁存** 至收盘；累计命中 `min_day_hits` 次（默认 2），且距首次 ≥ `min_span_minutes`（默认 3 分钟），且 **成交前再验** 条件仍成立 → 可执行。  
- 盘前产生的信号 **不计数**；计数从 **09:37** 第一次盘中调度起算。

---

### 3.5 卖出逻辑（仅盘中）

**标的范围**：仅 **持仓股**。  
已 **关闭**：评分低于 `sell_threshold` 卖出、持仓时间止损、日内走弱等。

当前仅两类 **原始卖出信号**：

| 类型 | 条件概要 | 执行时间 |
|------|----------|----------|
| **止损** | 浮亏 ≤ `stop_loss_pct`（**-7%**）**且** 趋势已破位（快口径）；豁免近涨停/当日强势 | 默认 **14:30 前不评估**；**趋近跌停** 可提前 |
| **趋势破位** | 加速仓：破 5 日线（**慢口径 3% + 双根确认**）/ 趋势衰竭；回调仓：破 MA20 | 非紧急卖须 **14:30 后** |

原则：**不破趋势不卖**；**让利润飞**（趋势退出用慢口径，容忍正常回踩、不被插针洗出）；避免早盘快照误触止损（如深跌后拉回）。

> **快/慢口径解耦（防闷杀）**：趋势退出走慢口径（`ma5_break_ratio=0.97` + 连续 2 根确认）让利润飞；**止损走快口径**（`ma5_break_ratio_fast=0.995`，单根即触发）——松绑 MA5 不会拖慢止损，"第一天破 MA5、第二天闷杀"由 -7% 快止损兜底。

卖出同样走 **持续确认**  pipeline，成交前 `verify_sell_signal_still_valid` 再验。

---

### 3.6 决策与叙述分工

| 内容 | 产出方 |
|------|--------|
| 加自选、移观察池、买卖、成交、仓位 | **规则引擎 + 模拟成交** |
| 大盘/概念榜/自选异动/操作段（盘中） | **模板** `during_market_push` |
| 新闻、盘前、午间/晚间复盘叙述 | **LLM**（注入 `engine_brief`，不得推翻引擎结论） |
| 阈值/权重离线优化 | **ML**（不参与盘中推理） |

---

## 四、运行量化机器人

### 4.1 五种模式（速查）

| 命令 | 时段 | 自选 | 买卖 |
|------|------|:----:|:----:|
| `python -m quant news` | 新闻 | — | — |
| `python -m quant pre_market` | 盘前 | 不改 | 信号 only |
| `python -m quant during_market` | 盘中 | 不改 | 可成交 |
| `python -m quant post_market_lunch` | 午间 | 不改 | — |
| `python -m quant post_market_evening` | 晚间 | **更新** | — |

### 4.2 单次执行示例

```powershell
# 1. 确保 API 已启动
python -m app

# 2. 另开终端，激活 venv 后执行（示例：晚间复盘）
python -m quant post_market_evening
```

每次运行会：拉取 API 数据 → 落盘 `~/.quant/daily/` → 按模式执行评分/交易/叙述 → 推送飞书。

### 4.3 建议调度

与内置调度器默认一致（工作日，`QUANT_SCHEDULER_ENABLED=true`）：

| 时间 | 模式 |
|------|------|
| 05:00 | 预取概念/粘合度（可选） |
| 07–23 部分整点（避开盘中/午间） | `news` |
| 09:25 | `pre_market` |
| **09:37–15:00 每 7 分钟** | `during_market` |
| 11:50 | `post_market_lunch` |
| 20:10 | `post_market_evening` |

也可用手动 cron / 任务计划调用 `python -m quant <mode>`；工作目录为项目根并激活 venv。

### 4.4 飞书推送格式

不变，示例：

```text
【晚间复盘】2026-05-26 15:10:00

一、大盘概况
…

六、自选更新
【新增自选】
· 某某股份（600xxx）评分72.5 [主升浪战法]
```

推送正文统一使用「赚钱效应强/一般/差」描述行情强弱，「仓位控制」及具体比例描述仓位上限；勿使用「市场环境」「市场档位」「可参与交易」等旧表述。

```text
【晚间复盘】2026-05-26 20:10:00

一、大盘概况
…

六、自选更新
· 某某股份，所属概念超级电容、储能、5G，创新高，评分72
本轮新入选：
· …
```

推送正文使用「赚钱效应强/一般/差」「仓位控制」等表述；勿使用已废弃的「市场环境」「可参与交易」等旧词。

「操作」「自选更新」由 **引擎确定性产出**；LLM 叙述须与 `engine_brief` 一致，不得虚构未出现的概念名。

### 4.5 历史回测

重放 `~/.quant/daily/{date}/raw/during*.json` 盘中快照，用 **与实盘一致的三确认 pipeline** 产出可执行信号，交模拟撮合器成交（佣金/印花税/过户费/滑点/涨跌停/T+1 齐全）。

```powershell
python -m quant.backtest --from 2026-06-17 --to 2026-06-26 --cash 100000
# 加 --json 输出机器可读结果
```

- **与实盘对齐**：走 `generate_confirmed_signals`，三确认的 `_now` 用快照时间、`signal_pending` 内存隔离（不污染实盘状态）。
- **估值**：持仓掉出当日自选快照时，用 akshare 日线真实收盘价估值（带缓存），权益曲线不失真。
- **输出**：交易笔数、卖出笔数、已实现盈亏、胜率、盈亏比、最大回撤、总回报、期末权益。

> 回测显著度取决于快照积累量；系统自身 ML 校准要求 ≥100 样本，建议至少覆盖一轮趋势 + 一轮震荡再下结论。

---

## 五、配置说明

### 5.1 评分与阈值

默认：`quant/config/quant.yml`（包内）  
用户覆盖：`~/.quant/config/quant.yml`（deep merge）

主要字段：

```yaml
watchlist_threshold: 70            # 参考中位（ML/floor 引用）
watchlist_entry_threshold: 72      # 新进自选 & 观察池恢复（hysteresis 上沿）
watchlist_exit_threshold: 66       # 连续未达标移观察池（下沿）；[66,72) 死区
buy_threshold: 72         # 买入最低分
sell_threshold: 45        # 保留；评分走弱卖已关闭
candidate:
  watchlist_retain_days: 3      # 连续未达标移观察池（交易日）
  watchlist_observe_max_days: 30
dimensions:               # 各维度 enabled + weight
  main_wave: { enabled: true, weight: 30 }
  concept_theme: { enabled: true, weight: 9 }
  ...
```

### 5.2 硬门禁与仓位

默认：`quant/config/quant.yml` 中的 `gates:` 段  
用户覆盖：`~/.quant/config/quant.yml`（deep merge）

含：标的池、极端熔断、每日亏损限额、止损冷却、分档仓位上限、**三确认**与 **买卖/卖出** 子配置等。

`trading.time_validation_enabled: false` 时任意时刻可模拟成交（联调用）；实盘请改为 `true`。

### 5.3 策略文档

`quant/strategy.md` 为策略条文；LLM 叙述时会注入相关章节，**买卖不由 LLM 决定**。

---

## 六、ML 离线校准

ML **不参与盘中推理**，仅在收盘后（或周末）用历史数据优化阈值与维度权重。

### 6.1 数据来源

自动扫描 `~/.quant/daily/*/derived/scores_watchlist.json`，结合：

- 次日行情涨幅（`daily/{next}/raw/*.json`）
- 后续成交盈亏（`daily/*/trades/executed.json`）

构建标签后做校准。**至少积累约 100 条样本**后再跑（默认 `--min-samples 100`）。

### 6.2 命令

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

### 6.3 生效方式

`--apply` 写入 `~/.quant/config/ml_calibration.yml`，下次 `python -m quant` 启动时自动合并到评分配置（优先级高于包内默认值）。

> 注：ML 目前只优化单一的 `watchlist_threshold`（参考中位）。自选 hysteresis 的上下沿（`watchlist_entry_threshold` / `watchlist_exit_threshold`）为固定值，不随 ML 调整——如需让数据定死区宽度，可后续扩展校准目标。

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

## 七、量化数据 API（OpenClaw 入口）

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

## 八、常见问题

**Q：quant 报连接失败？**  
A：先确认 `python -m app` 已启动，且 `quant/config.py` 中 `BASE_URL` 与 API 端口一致（默认 `http://localhost:8085`）。

**Q：旧版 `~/.quant/optional.jsonl` 在哪？**  
A：已改为 `~/.quant/state/optional.jsonl`，请手动迁移或重新跑晚间复盘生成自选。

**Q：ML 提示样本不足？**  
A：多运行若干交易日，确保每天晚间复盘产生 `daily/{date}/derived/scores_watchlist.json`。

**Q：`.env` 端口不生效？**  
A：使用 `python -m app` 启动；裸 `uvicorn` 需显式 `--port`，见 `.env.example` 说明。

---

## 九、本地自检

```powershell
curl http://127.0.0.1:8085/health
curl http://127.0.0.1:8085/api/v1/quant/market/pre_market
python -m quant.ml calibrate --method grid --dry-run
```

---

## 许可证与致谢

- 项目代码以仓库许可为准。  
- 数据版权归各提供方所有；感谢 [AKShare](https://github.com/akfamily/akshare)。
