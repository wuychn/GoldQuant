# 环境变量说明

> 前缀均为 **`GOLDQUANT_`**（Pydantic Settings）；飞书、LLM 部分键支持无前缀。  
> 策略与调度见 [CONFIG.md](./CONFIG.md)，不在 `.env` 配置。

复制模板：`copy .env.example .env`

---

## 1. 应用与 API

| 变量 | 默认 | 说明 |
|---|---|---|
| `GOLDQUANT_ENV` | local | 运行环境标识 |
| `GOLDQUANT_API_V1_STR` | /api/v1 | API 前缀 |
| `GOLDQUANT_HOST` | 0.0.0.0 | 监听地址 |
| `GOLDQUANT_PORT` | 8085 | 监听端口（`python -m app` 读取） |
| `GOLDQUANT_UVICORN_RELOAD` | true | 开发热重载 |
| `GOLDQUANT_CORS_ORIGINS` | * | CORS 源（逗号分隔） |
| `GOLDQUANT_CORS_ALLOW_CREDENTIALS` | false | 与 `*` 互斥 |

读取：`app/core/config.py:Settings`

**注意**：裸 `uvicorn app.main:app` 不读 `GOLDQUANT_PORT`，需 `--port` 或改用 `python -m app`。

---

## 2. HTTP / 代理

| 变量 | 默认 | 说明 |
|---|---|---|
| `GOLDQUANT_HTTP_CLIENT_TIMEOUT` | 30 | HTTP 超时（秒） |
| `GOLDQUANT_THS_DEFAULT_USER_AGENT` | Chrome UA | 同花顺请求 UA |
| `GOLDQUANT_PROXY_ENABLED` | false | 出站代理开关 |
| `GOLDQUANT_PROXY_URL` | — | 代理 URL |
| `GOLDQUANT_PROXY_HTTP_URL` | — | HTTP 代理（可选） |
| `GOLDQUANT_PROXY_HTTPS_URL` | — | HTTPS 代理（可选） |
| `GOLDQUANT_PROXY_NO_PROXY` | localhost,127.0.0.1 | 不走代理列表 |

读取：`app/core/proxy.py:apply_process_proxy`

---

## 3. 量化运行时

| 变量 | 默认 | 说明 |
|---|---|---|
| `GOLDQUANT_QUANT_HOME_DIR` | ~/.quant | 量化数据根（或 shell `QUANT_HOME`） |
| `GOLDQUANT_QUANT_HIST_FULL_START_DATE` | 19900101 | 日线全量起点 |
| `GOLDQUANT_QUANT_HIST_INCREMENTAL_TRADE_DAYS` | 5 | 增量重叠交易日 |
| `GOLDQUANT_QUANT_SPOT_EM_FULL_TABLE` | false | 盘前是否拉全市场 spot |
| `GOLDQUANT_QUANT_HIST_RESPONSE_MAX_BARS` | 48 | API 返回 K 线上限 |
| `GOLDQUANT_QUANT_HIST_SCORING_MAX_BARS` | 90 | 评分用 K 线 |
| `GOLDQUANT_QUANT_USE_LOCAL_FIXTURE` | false | quant CLI 读本地 fixture |
| `GOLDQUANT_QUANT_TEST_PHASE` | false | 列表统一截断 3 条 |
| `GOLDQUANT_QUANT_ENRICH_CONCURRENCY` | 6 | enrich 并发 |
| `GOLDQUANT_QUANT_ENRICH_EVENING_*` | 见 Settings | 晚间 enrich 批次/暂停 |
| `GOLDQUANT_QUANT_THS_FUNDS_*` | 见 Settings | 同花顺资金流缓存/重试 |

读取：`quant/store/paths.py:quant_home()`, `app/core/config.py`

**解析优先级**（quant_home）：

1. `override_quant_home` 上下文（纸面账户）
2. `GOLDQUANT_QUANT_HOME_DIR`
3. `QUANT_HOME`（无前缀，shell 临时覆盖）
4. `~/.quant`

---

## 4. 飞书

| 变量 | 前缀 | 说明 |
|---|---|---|
| `FEISHU_APP_ID` | 可无 | 飞书应用 ID |
| `FEISHU_APP_SECRET` | 可无 | 飞书应用密钥 |
| `FEISHU_USER_ID` | 可无 | 接收人 open_id |

读取：`Settings` / `quant/config.get_feishu_config()`

---

## 5. LLM

| 变量 | 前缀 | 说明 |
|---|---|---|
| `LLM_API_KEY` | 可无 | API 密钥 |
| `LLM_BASE_URL` | 可无 | 接口地址 |
| `LLM_MODEL` | 可无 | 模型名 |
| `GOLDQUANT_LLM_API_FORMAT` | GOLDQUANT_ | openai / anthropic |
| `GOLDQUANT_LLM_VERIFY_SSL` | 可无 | HTTPS 校验 |
| `GOLDQUANT_LLM_TRUST_ENV_PROXY` | 可无 | 读系统代理 |
| `GOLDQUANT_LLM_TIMEOUT_SEC` | 600 | 超时 |
| `GOLDQUANT_LLM_USE_APP_PROXY` | false | 走应用代理 |

读取：`app/utils/llm_client.py`（仅 `news` 模式使用，不参与交易决策）

---

## 6. 已废弃 / 已迁移

| 旧变量 | 替代 |
|---|---|
| `QUANT_SCHEDULER_ENABLED` | `quant.yml` → `scheduler.enabled` |
| `QUANT_SCHED_*` | `quant.yml` → `scheduler.*` |
| 策略阈值、组合参数 | `quant.yml` → `gates` / `portfolio` |
| weekly ML/backtest 调度 | 改手动 `scripts/research` / `scripts/backtest` |

---

## 7. 变量 → 代码映射

| 类别 | 读取位置 |
|---|---|
| 应用/API | `app/core/config.py:Settings` |
| 量化 Home | `quant/store/paths.py:quant_home()` |
| 代理 | `app/core/proxy.py` |
| 飞书 | `quant/ops/push.py`, `quant/config.get_feishu_config` |
| LLM | `app/utils/llm_client.py`, `quant/narrative/llm.py` |
| Fixture | `quant/data_fetch.py` |
| Enrich | `app/services/stock_enrich.py` |
