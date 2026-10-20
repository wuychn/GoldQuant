# GoldQuant 配置参考

所有配置项的唯一说明。两类文件按性质分工，**不是散落**：

| 文件 | 性质 | 加载入口 | 内容 |
|------|------|----------|------|
| `.env` | 部署 / 敏感 / 环境 | `common.config.get_settings()` | 端口、密钥、代理、路径、飞书 |
| `quant/config/quant.yml` | 业务 | `quant.config.load_quant_config()` | 标的前缀、候选池、数据源、调度 |

- `.env` 在项目根；前缀 `GOLDQUANT_`（`FEISHU_*` 支持无前缀）。
- `quant.yml` 包内默认在 `quant/config/quant.yml`；用户覆盖 `~/.quant/config/quant.yml`（deep merge）。

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

### Uvicorn 启动（`poetry run python -m app`）

| 项 | 默认 | 作用 |
|----|------|------|
| `GOLDQUANT_HOST` | `0.0.0.0` | 监听地址 |
| `GOLDQUANT_PORT` | `8085` | API 端口 |
| `GOLDQUANT_UVICORN_RELOAD` | `true` | 热重载；生产设 `false` |

> 裸 `uvicorn app.main:app` 不读 `GOLDQUANT_PORT`；请用 `poetry run python -m app` 或显式 `--port`。

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
| `GOLDQUANT_QUANT_HOME_DIR` | 空→`~/.quant` | 量化数据根目录（state/views/daily/config/memory/cache 均在其下） |

**`quant_home()` 解析优先级**（高 → 低）：

1. `override_quant_home` 上下文（纸面账户 / 测试隔离）
2. `GOLDQUANT_QUANT_HOME_DIR`
3. `QUANT_HOME`（无 `GOLDQUANT_` 前缀，便于 shell 临时覆盖）
4. `~/.quant`（默认）

> `quant/store/paths.py` 模块级常量 `QUANT_HOME` 仅在导入时刻解析一次，不跟踪运行期 override；运行期取 home 一律调 `quant_home()`。

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
| `GOLDQUANT_QUANT_USE_LOCAL_FIXTURE` | `false` | `true` 时 `poetry run python -m quant` 读 `data/*.json`，不调 service |

### 飞书推送

| 项 | 默认 | 作用 |
|----|------|------|
| `FEISHU_APP_ID` | 空 | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 空 | 飞书应用密钥 |
| `FEISHU_USER_ID` | 空 | 接收人 open_id |

5 板复盘和盘中指令走同一套飞书配置。状态文件：`$QUANT_HOME/store/tactic/state.json`。

### 变量 → 代码映射

| 类别 | 读取位置 |
|---|---|
| 应用 / API | `common/config.py` |
| 量化 Home | `quant/store/paths.py` |
| 飞书 | `quant/ops/push.py`、`quant/config.get_feishu_config` |

---

## 二、`quant.yml`

包内 `quant/config/quant.yml`。用户文件 `~/.quant/config/quant.yml` 做 deep merge。

### `gates.symbol_pool`

日线入库时过滤代码前缀。`prefixes` 默认 60 / 00 / 30 / 688。`exclude_st` 剔除 ST。

### `data`

并发上限，以及 `sources.daily` / `market` / `enrich` / `info` 的接口级换源。一个方法只走列表里的源。

5 板相关：

| 接口 | 源 |
|---|---|
| `fetch_spot` / `fetch_hist` / `fetch_index` | sina，失败再 akshare |
| `fetch_zt_pool_hist` / 炸板 / 跌停 / 昨涨停 | eastmoney |
| `fetch_severe_anomaly` | eastmoney |
| `fetch_lhb_day` | eastmoney（只归档，策略不读） |

### `scheduler`

复盘和盘中由 Windows 计划任务执行，`python -m app` 不注册。`quant.yml` 里仍保留时间字段，当前不读取。

| 键 | 默认 | 作用 |
|---|---|---|
| `daily_decision_time` | 20:10 | 复盘时刻（Windows 任务 GoldQuantTacticClose） |
