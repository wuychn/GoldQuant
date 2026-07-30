# GoldQuant 配置参考

所有配置项的唯一说明。两类文件按性质分工，**不是散落**：

| 文件 | 性质 | 加载入口 | 内容 |
|------|------|----------|------|
| `.env` | 部署 / 敏感 / 环境 | `common.config.get_settings()` | 端口、密钥、代理、路径、并发、LLM、飞书 |
| `quant/config/quant.yml` | 业务 / 策略 | `quant.config.load_quant_config()` | 门禁、组合、候选、调度、数据源 |

- `.env` 在项目根；前缀 `GOLDQUANT_`（`LLM_*` / `FEISHU_*` 支持无前缀）。
- `quant.yml` 包内默认在 `quant/config/quant.yml`；用户覆盖 `~/.quant/config/quant.yml`（deep merge）。
- 因子权重单独两文件（见末尾）。

加载机制：
- `common.config.Settings`（pydantic BaseSettings，`env_prefix=GOLDQUANT_`，读项目根 `.env`）。所有代码取环境配置走 `get_settings()`。
- `quant.config.load_quant_config()` 读包内 `quant.yml` + deep merge 用户 `~/.quant/config/quant.yml`，经 `quant/yml_schema.py` 校验。取业务配置走它。
- 优先级：环境变量 > `.env` 文件 > 代码默认值。

---

## 一、`.env` 配置项

### 应用元信息 / API

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_ENV` | `local` | 运行环境标识（仅 `/` 根路径展示） |
| `GOLDQUANT_API_V1_STR` | `/api/v1` | API 路由前缀 |

### Uvicorn 启动（`python -m app`）

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_HOST` | `0.0.0.0` | 监听地址 |
| `GOLDQUANT_PORT` | `8085` | API 端口 |
| `GOLDQUANT_UVICORN_RELOAD` | `true` | 热重载；生产设 `false` |

> 裸 `uvicorn app.main:app` 不读 `GOLDQUANT_PORT`；请用 `python -m app` 或显式 `--port`。

### CORS

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_CORS_ORIGINS` | `*` | 单个 `*` 或逗号分隔多源 |
| `GOLDQUANT_CORS_ALLOW_CREDENTIALS` | `false` | `ORIGINS=*` 时强制 `false` |

### HTTP 客户端（同花顺等直连）

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_HTTP_CLIENT_TIMEOUT` | `30` | 请求超时（秒） |
| `GOLDQUANT_THS_DEFAULT_USER_AGENT` | Chrome 120 UA | 同花顺请求 UA |

### 出站代理（AKShare / 同花顺 / httpx 均走此）

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_PROXY_ENABLED` | `false` | 总开关 |
| `GOLDQUANT_PROXY_URL` | 空 | HTTP+HTTPS 默认代理，如 `http://127.0.0.1:7890` |
| `GOLDQUANT_PROXY_HTTP_URL` / `GOLDQUANT_PROXY_HTTPS_URL` | 空 | 按协议分别覆盖（留空回退 `PROXY_URL`） |
| `GOLDQUANT_PROXY_NO_PROXY` | `localhost,127.0.0.1` | 不走代理的地址 |

### 量化运行时目录

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_QUANT_HOME_DIR` | 空→`~/.quant` | 量化数据根目录（state/views/daily/config/memory/cache 均在其下）。shell `export QUANT_HOME=...` 也可，但优先级**低于**此项 |

### 历史日线拉取

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_QUANT_HIST_FULL_START_DATE` | `19900101` | 本地无日线时全量起始日 |
| `GOLDQUANT_QUANT_HIST_INCREMENTAL_TRADE_DAYS` | `5` | 末根已是今天时向前重叠拉取的交易日数 |
| `GOLDQUANT_QUANT_SPOT_EM_FULL_TABLE` | `false` | 盘前是否拉全市场 `spot_em` 表（数据大、易限流） |
| `GOLDQUANT_QUANT_HIST_RESPONSE_MAX_BARS` | `48` | 接口「历史行情」日线返回条数上限 |
| `GOLDQUANT_QUANT_HIST_SCORING_MAX_BARS` | `90` | 候选 enrich 历史行情保留交易日数 |

### Enrich 并发

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_QUANT_ENRICH_CONCURRENCY` | `6` | 盘中/盘后自选 enrich 并发 |
| `GOLDQUANT_QUANT_ENRICH_EVENING_CONCURRENCY` | `6` | 晚间复盘 enrich 批内并发 |
| `GOLDQUANT_QUANT_ENRICH_EVENING_BATCH_SIZE` | `20` | 晚间每批 enrich 只数（0=不分批） |
| `GOLDQUANT_QUANT_ENRICH_EVENING_BATCH_PAUSE_SEC` | `600` | 晚间批次间隔秒数 |

### 同花顺个股实时资金流

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_QUANT_THS_FUNDS_CACHE_TTL_SEC` | `120` | 内存缓存 TTL |
| `GOLDQUANT_QUANT_THS_FUNDS_RETRY_MAX` | `3` | 重试次数 |
| `GOLDQUANT_QUANT_THS_FUNDS_RETRY_BASE_SEC` | `1` | 重试基础退避（实际 = base×2^attempt + 累计失败×backoff） |
| `GOLDQUANT_QUANT_THS_FUNDS_FAILURE_BACKOFF_SEC` | `5` | 每累计一次失败，后续额外等待秒数 |

### 测试阶段

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_QUANT_TEST_PHASE` | `false` | `true` 时 API/enrich/推送/state 列表统一仅 3 条 |
| `GOLDQUANT_QUANT_USE_LOCAL_FIXTURE` | `false` | `true` 时 `python -m quant` 读 `data/*.json`，不调 service |

### 飞书推送

| 项 | 默认 | 作用 |
|----|------|------|
| `FEISHU_APP_ID` | 空 | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 空 | 飞书应用密钥 |
| `FEISHU_USER_ID` | 空 | 接收人 open_id |

### LLM（新闻解读 / 复盘叙述）

| 项 | 默认 | 作用 |
|----|------|------|
| `LLM_API_KEY` | 空 | 密钥（支持无前缀或 `GOLDQUANT_LLM_API_KEY`） |
| `LLM_BASE_URL` | `https://api.minimaxi.com/anthropic` | 接口地址（勿含 `chat/completions` 或 `v1/messages`） |
| `LLM_MODEL` | `MiniMax-M2.7` | 模型名 |
| `GOLDQUANT_LLM_API_FORMAT` | `openai` | `openai`→`/chat/completions`；`anthropic`→`/v1/messages` |
| `GOLDQUANT_LLM_VERIFY_SSL` | `false` | 内网/自签证书设 `false` |
| `GOLDQUANT_LLM_TRUST_ENV_PROXY` | `false` | 是否读系统 `HTTP_PROXY`（干扰时设 `false`） |

---

## 二、`quant.yml` 配置项

### `gates` — 撮合与风控门禁（`quant/execution/sim_rules.py`）

#### `gates.trading` — 交易时段与日内风控

| 项 | 默认 | 作用 |
|----|------|------|
| `time_validation_enabled` | `true` | `true` 仅交易时段允许成交（实盘保持 `true`；联调可 `false`） |
| `daily_loss_limit_pct` | `-3.0` | 日内权益回撤 ≤ -3% 禁开仓 |
| `stoploss_cooldown_days` | `3` | 止损后 3 交易日不再买同码 |
| `block_same_day_rebuy_after_sell` | `true` | 当日卖后禁同日回补 |

#### `gates.trading.simulation` — 撮合成本与滑点

| 项 | 默认 | 作用 |
|----|------|------|
| `commission_rate` | `0.0001` | 佣金万一 |
| `min_commission` | `5.0` | 佣金最低 5 元 |
| `stamp_tax_rate` | `0.0005` | 卖出印花税万五 |
| `transfer_fee_rate` | `1.0e-05` | 沪市过户费 |
| `slippage_pct` | `0.001` | 基础滑点 |
| `slippage_model` | `sqrt_law` | 按成交额平方根放大 |
| `slippage_sqrt_k` | `0.5` | sqrt_law 系数 |
| `slippage_max_pct` | `0.008` | 滑点上限 |
| `partial_fill_enabled` | `true` | 超 ADV 参与率时分笔成交 |
| `participation_rate` | `0.1` | 买单 ADV 参与率封顶 10% |

#### `gates.symbol_pool` — 标的池

| 项 | 默认 | 作用 |
|----|------|------|
| `prefixes` | `['60','00','30']` | 沪主/深主/创业板 |
| `exclude_st` | `true` | 剔除 ST/*ST |

#### `gates.circuit_breaker` — 大盘熔断

| 项 | 默认 | 作用 |
|----|------|------|
| `index_drop_pct` | `-2.0` | 指数跌 ≤ -2% 禁开仓 |

#### `gates.sell` — 卖出时段门禁

| 项 | 默认 | 作用 |
|----|------|------|
| `limit_up_exempt_margin_pct` | `0.5` | 涨停板附近 0.5% 内豁免 |
| `limit_down_approach_margin_pct` | `1.0` | 跌停板附近 1% 内禁卖 |
| `late_session_after` | `14:30` | 尾盘起点 |
| `late_session_final_only` | `true` | 尾盘只处理指定 kinds |
| `late_session_kinds` | `[破5日线, 趋势衰竭]` | 尾盘允许的卖出原因 |

### `research.factors` — 离线研究

| 项 | 默认 | 作用 |
|----|------|------|
| `strict_oos_weights` | `true` | `true` 时 live 不用静态全样本权重，仅 walk-forward OOS |

### `portfolio` — 目标组合（`quant/portfolio/target.py`）

| 项 | 默认 | 作用 |
|----|------|------|
| `optimizer` | `mvo` | 优化器：均值-方差 |
| `optimizer_mvo.risk_aversion` | `2.0` | MVO 风险厌恶系数 |
| `covariance.method` | `ewma` | 协方差估计：指数加权 |
| `risk_budget.vol_lookback` | `14` | 已实现波动率回看窗口（交易日） |
| `constraints.max_concept_pct` | `40` | 单概念 ≤ 40% |
| `constraints.max_industry_pct` | `40` | 单行业 ≤ 40% |
| `style_exposure.alpha_weighted` | `true` | 按 alpha 加权风格 |
| `style_exposure.alpha_shrink` | `0.4` | alpha 收缩系数 |
| `style_exposure.max_small_cap_pct` | `50` | 小盘暴露 ≤ 50% |
| `style_exposure.max_high_mom_pct` | `60` | 高动量暴露 ≤ 60% |
| `risk.max_drawdown_halt_enabled` | `true` | 回撤熔断开关 |
| `risk.max_drawdown_pct` | `15` | 回撤 ≤ -15% halt |
| `risk.halt_days` | `5` | 停买 5 交易日 |

> 另有 `n_enter=8 / n_exit=15 / max_stocks=10 / full_invest=0.95 / target_vol=0.15 / max_weight=0.25` 等在 `TargetPortfolio.from_config` 默认，见 [ARCHITECTURE.md §6](./ARCHITECTURE.md)。

### `candidate` — 候选池（展示用，非 alpha 选股）

| 项 | 默认 | 作用 |
|----|------|------|
| `popularity_limit` | `20` | 东财人气榜取前 20 |
| `zt_min_boards` | `3` | 涨停池最少连板 |
| `cxg_labels` | 创月/半年/一年/历史新高 | 创新高标签 |
| `include_zt_pool` | `false` | 是否纳入涨停池 |
| `include_ths_rank_pool` | `true` | 是否纳入同花顺榜 |
| `universe.min_adv_yi` | `1.0` | 20 日 ADV ≥ 1 亿 |
| `universe.adv_lookback` | `20` | ADV 回看窗口 |
| `universe.max_crowding_rank` | `null` | 拥挤度排名上限（null 不限） |
| `universe.participation_rate` | `0.1` | 候选池参与率 |

### `data` — 数据源并发限流与数据源选择

并发限流（`quant/data/sources/rate_limit.py`，按厂商令牌桶）：

| 项 | 默认 | 作用 |
|----|------|------|
| `default_max_concurrent` | `1` | 默认并发 |
| `akshare_max_concurrent` | `1` | AKShare 并发（过高易限流） |
| `ths_max_concurrent` | `1` | 同花顺并发 |
| `eastmoney_max_concurrent` | `1` | 东财并发 |

数据源选择（换源只改配置、不改代码；四类协议各一个 `default` 组合实现）：

| 项 | 默认 | 作用 |
|----|------|------|
| `sources.daily` | `default` | 日线库数据源（构建/维护离线库：hist/index/calendar/code_list/delisted/spot） |
| `sources.market` | `default` | 运维行情数据源（五时段 payload 宏观：index_spot/zqxy/ztgk/hot/concept/industry/fund_flow） |
| `sources.enrich` | `default` | 个股 enrich 数据源（盘口/资金流/概念粘合度/问财概念/分钟K） |
| `sources.info` | `default` | 资讯/基本信息数据源（全局新闻/个股基本信息 jbxx） |

> 调用方走 facade（`get_daily_source()` / `get_market_source()` / `get_enrich_source()` /
> `get_info_source()`），零感知后端实现。详见 `docs/ARCHITECTURE.md` 数据源章节。

### `scheduler` — APScheduler（`app/scheduling/quant_scheduler.py`）

| 项 | 默认 | 作用 |
|----|------|------|
| `enabled` | `true` | 总开关；`false` 不启动调度器 |
| `timezone` | `Asia/Shanghai` | 时区 |
| `news_hours` | `8..22` | 新闻整点小时 |
| `news_minute` | `0` | 新闻触发分钟 |
| `pre_market_time` | `09:25` | 盘前 |
| `during_market_times` | `09:37` 起每 7 分钟 | 盘中时点列表 |
| `post_market_lunch_time` | `11:50` | 午间复盘 |
| `maintain_daily_time` | `16:00` | 收盘后数据维护（须早于 daily_decision） |
| `daily_decision_time` | `20:10` | 晚间选股定计划 |
| `prefetch_concepts_enabled` | `true` | 是否预取概念/基本信息 |
| `prefetch_concepts_time` | `05:00` | 预取时点 |
| `misfire_grace_sec` | `600` | 漏触补跑容忍窗口 |
| `in_process_modes` | news/pre/during/lunch/evening | 这些模式在 app 进程内直接调 service；其余走子进程 |

---

## 三、因子权重文件（`~/.quant/config/`）

| 文件 | 加载 | 作用 |
|------|------|------|
| `factor_weights_ts.yml` | `load_factor_weights_info(as_of)` 优先 | walk-forward OOS 时变权重（live 优先） |
| `factor_weights.yml` | 上述缺失时回退 | 静态 IC 权重（`strict_oos_weights=true` 且有 `as_of` 时不用于 live） |

两者结构：`{apply: bool, weights: {因子名: 权重}, meta: {...}, fit_end: "YYYY-MM-DD"}`（ts 版为 `weights_ts: {日期: {weights, ...}}`）。由 `scripts/factors/fit_weights.py` / `scripts/research/walk_forward.py` 产出。

无上述文件时回退 `quant.factors.registry.REGISTRY` 默认权重（推送会标注 "registry 默认，未校准"）。
