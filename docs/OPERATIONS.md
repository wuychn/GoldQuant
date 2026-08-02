# 运维与操作指南

> 依赖由 **Poetry** 管理（`pyproject.toml` + `poetry.lock`）。下文命令一律用 `poetry run python -m ...`，避免误用系统 Python。

## 1. 环境要求

- Python **3.11+**（推荐 3.11；`requires-python >=3.11,<3.14`）
- [Poetry](https://python-poetry.org/) 2.x
- 可访问外网（拉取行情）
- Windows / Linux 均可

---

## 2. 安装

在项目根目录 `GoldQuant` 下：

```powershell
# 安装主依赖（Poetry 会使用/创建项目 .venv）
poetry install

# 需要 ML 离线校准（IC/拟合等）时再装可选组
poetry install --extras ml

copy .env.example .env
```

编辑 `.env`：至少配置 API 端口、飞书、LLM（见 [CONFIG.md](./CONFIG.md)）。

> 等价写法：激活后直接调解释器  
> `poetry env activate`（或 `.\.venv\Scripts\Activate.ps1`）→ `python -m ...`  
> 与 `poetry run python -m ...` 使用同一环境。

---

## 3. 启动数据 API

**必须在项目根目录执行。**

- 仅当需要 **内置调度器** 自动跑五时段、或要访问 HTTP API / Swagger 时，才需启动 app。
- 单次 `poetry run python -m quant <mode>` **直调 service**，不经 HTTP，**不必**先启 API。

```powershell
# 推荐（读取 .env 端口）
poetry run python -m app

# 或（需自行带 host/port；不读 GOLDQUANT_PORT）
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8085
```

- Swagger：<http://127.0.0.1:8085/docs>
- 健康检查：<http://127.0.0.1:8085/health>

Linux 后台常驻：

```bash
chmod +x run.sh
./run.sh start
```

（`run.sh` / `run.ps1` 调用项目 `.venv` 中的 Python，需先 `poetry install`。）

**Fixture 模式**：设 `GOLDQUANT_QUANT_USE_LOCAL_FIXTURE=true`，quant CLI 读 `data/fixtures/*.json`，不请求外网/service（联调/离线测试）。

---

## 4. 运行量化机器人

### 4.1 CLI 模式

```powershell
# 运维推送
poetry run python -m quant news
poetry run python -m quant pre_market
poetry run python -m quant during_market          # 盘中先卖后买 + 推送
poetry run python -m quant post_market_lunch
poetry run python -m quant post_market_evening

# 日决策（T 晚选股 + 作战池/卖出监控 + 推送）
poetry run python -m quant daily_decision
poetry run python -m scripts.decision.daily --no-push    # 仅落盘
poetry run python -m scripts.decision.daily --dry-run     # 不撮合

# 预取概念/基本信息（可选）
poetry run python -m quant prefetch_concepts
```

`scripts.decision.daily` 常用参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--date` | 今天 | 决策日 YYYY-MM-DD |
| `--out` | `$QUANT_HOME/reports/decision` | 报告目录 |
| `--n-enter` / `--n-exit` | 8 / 15 | 排名 buffer |
| `--max-positions` | 10 | 最大持股 |
| `--battle-pool-size` | 30 | 作战池规模（alpha top N） |
| `--no-paper` | — | 仅决策卡，不撮合 |
| `--dry-run` | — | 干跑 |
| `--no-push` | — | 不推飞书 |

### 4.2 建议调度（quant.yml → scheduler）

| 时间 | 模式 | 说明 |
|---|---|---|
| 05:00 | `prefetch_concepts` | 预取持仓/自选概念与 jbxx |
| 8–22 每个整点 | `news` | LLM 新闻摘要 |
| 09:25 | `pre_market` | 盘前推送 |
| 09:37–15:00 每 7 分钟 | `during_market` | 盘中择时买卖 |
| 11:50 | `post_market_lunch` | 午间复盘 |
| 16:00 | `maintain` | 离线库维护 |
| 20:10 | `daily_decision` | T 晚选股 |

启动 API 时若 `scheduler.enabled: true`，`app/scheduling/quant_scheduler.py` 会自动注册上述任务。也可 cron / 任务计划手动调用：

```text
# 示例：指向 Poetry 创建的同一解释器
D:\workspace\GoldQuant\.venv\Scripts\python.exe -m quant during_market
```

### 4.3 飞书推送事件

| 标签 | 模式 | 要点 |
|---|---|---|
| 新闻聚焦 | `news` | LLM 去噪 + 综合解读 |
| 开盘啦 | `pre_market` | 指数 / 纸面账户 / 关注 |
| 智能盯盘 | `during_market` | 指数 / 持仓 / 异动 + **盘中买卖** |
| 午间复盘 | `post_market_lunch` | 午前指数 + 纸面账户 |
| 收盘复盘 | `post_market_evening` | 收盘指数 + 纸面绩效 |
| 晚间复盘 | `daily_decision` | 明日作战池 / 卖出监控 / 账户 |

格式：`quant/push/format.py` — 纯文本，`标题 + 时间 + 【小节】要点`。

---

## 5. 离线库构建与维护

离线库是因子计算、日决策、回测的**共同依赖**。

### 5.1 目录结构

数据根：`$QUANT_HOME/data/`（与 `quant/data/store.py` 一致）

| 内容 | 说明 |
|---|---|
| `daily_raw/` | 不复权 OHLCV + 市值等 |
| `adj_factor/` | 后复权因子 |
| `universe/` | 每日 universe 快照 |
| `industry/` | 行业 PIT |
| `fundamental_pit/` | 财务 PIT |
| `index_daily/` | 指数日线 |
| `calendar.parquet` | 交易日历 |

### 5.2 首次建库（手动，建议夜间）

```powershell
poetry run python -m scripts.data.build_daily --start 2021-01-01 --workers 1 --req-interval 5,10
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--start` | `2021-01-01` | 起始日 YYYY-MM-DD |
| `--end` | 今天 | 结束日 YYYY-MM-DD |
| `--workers` | `3` | 并发数（建议 1–3；过高易被东财限流） |
| `--req-interval` | `1,3` | 东财请求间隔秒，`MIN,MAX` 或单值 `N`；降频如 `5,10` 避频控 |
| `--limit` | — | 只拉前 N 只（调试） |
| `--codes` | — | 逗号分隔代码列表（调试） |
| `--ignore-existing` | — | 跳过完整性检查，强制全拉；并跳过市场缺口第二轮 |
| `--no-gap-fill` | — | 只做代码级续传，不做市场级缺失交易日第二轮 |
| `--no-delisted` | — | 不并入退市股（默认并入，修幸存者偏差） |
| `--no-adj` | — | 跳过后复权因子全量初始化 |
| `--retry-failed` | — | 只重试 `build_failed.jsonl` 中的失败 code |
| `--dead-threshold` | `5` | 失败次数达此值标 dead，不再自动重试 |

**默认智能断点续传（推荐反复跑同一命令直到完成）：**

1. **逐只完整性检查**：相对 `[start,end]`（默认 `end=今天`，与 `--start 2021-01-01` 等 CLI 一致），**缺任意一天**则补拉；齐全则跳过。若有 `listing_dates`，期望从 `max(start, 上市日)` 起算。
2. **补拉窗口 = 完整检查区间**：待拉码按 `{start}~{end}` 整段请求（write 去重，不重复堆行）。停牌等源站无 K 线的交易日记入 `$QUANT_HOME/data/no_bar_dates.json` 豁免，避免反复补拉。
3. **自动并入** `build_failed.jsonl` 中非 dead 失败码
4. **市场级第二轮**：若日历上存在「全市场都没有数据」的交易日，对该缺口窗全代码补拉；`--no-gap-fill` / `--ignore-existing` / `--retry-failed` 时跳过

- 拉全 A 历史（`stock_zh_a_hist` 不复权）+ 指数 + 日历 + 退市股
- 中断后或隔几天未做增量，再执行同一命令即可按完整性续补到 `end`
- 日常增量仍推荐 `update_daily` / `maintain`；`build_daily` 适合建库与查漏补缺

**常见告警：**

| 现象 | 原因 | 处理 |
|---|---|---|
| `ProxyError` / `Unable to connect to proxy` | Clash 等注入了系统 `HTTP(S)_PROXY`，请求被劫持 | 默认 `PROXY_ENABLED=false` 时已显式禁用系统代理；确认 `.env` 未误开代理即可重跑 |
| `Couldn't deserialize thrift` / `Unexpected end of stream` | 多线程并发写同一 `year=*/part.parquet` 曾写坏分区 | 现已文件锁+原子写；读到损坏会改名为 `part.parquet.corrupt.<ts>`。重跑同一 `build_daily` 命令即可回补丢失年份 |

若本地已有损坏分区，也可手动删掉对应 `year=*/part.parquet`（或保留 `.corrupt.*` 备份）后重跑续传。

强制补一段日期（跳过智能扫描）：

```powershell
poetry run python -m scripts.data.build_daily --start 2026-07-20 --end 2026-07-25 --ignore-existing --workers 1 --req-interval 5,10
```

### 5.3 日增量

```powershell
poetry run python -m scripts.data.update_daily
poetry run python -m scripts.data.update_daily --date 2026-07-25
poetry run python -m scripts.data.update_daily --force-fundamental-pit
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--date` | 今天 | 指定日 YYYY-MM-DD |
| `--force-fundamental-pit` | — | 无视披露季窗口，增量刷新 fundamental_pit |

收盘后执行：`spot_em` → 追加当日 daily_raw、除权检测、复权刷新、universe/行业/listing、fundamental_pit、因子快照（flow/hot/theme）。

**注意**：spot_em 无历史，当日没抓就补不回来，失败须告警。

### 5.4 自动维护（推荐）

```powershell
poetry run python -m scripts.data.maintain
poetry run python -m scripts.data.maintain --date 2026-07-25
poetry run python -m scripts.data.maintain --start 2021-01-01
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--date` | 今天 | 维护截至日（非交易日回退最近交易日） |
| `--start` | `2021-01-01` | 无库时全量起始日 |

逻辑（`scripts/data/maintain.py`）：

1. 库为空 → 全量 `build_daily`
2. 有库 → 扫描交易日历缺口 → `build_daily --ignore-existing` 回补
3. 最后跑 `update_daily` 当日增量

默认由调度器 **16:00** 触发。

### 5.5 其他数据脚本

| 命令 | 主要参数 | 作用 |
|---|---|---|
| `poetry run python -m scripts.data.build_fundamental_pit` | `--codes` `--limit` `--batch-size` `--sleep` `--retries` `--resume` | 批量建财务 PIT |
| `poetry run python -m scripts.data.build_listing_dates` | （无 CLI 参数） | 上市日表 |
| `poetry run python -m scripts.data.backfill_factor_snapshots` | `--start` `--end`（必填） | 因子快照回填 |
| `poetry run python -m scripts.data.verify_daily` | `--sample` `--start` `--end` | 数据校验 |
| `poetry run python -m scripts.data.audit_data_health` | `--probe-code` | 健康审计 |

### 5.6 复权说明

- 因子/回测/出场：**必须**用 `load_adjusted_daily()`（后复权）
- 撮合/涨跌停：用 daily_raw 真实价
- 除权检测：`detect_ex_dividend_codes` → `refresh_adj_for_codes`

---

## 6. 回测

### 6.1 主回测

```powershell
poetry run python -m scripts.backtest.run --start 2024-01-01 --end 2024-06-30
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--start` / `--end` | 必填 | YYYY-MM-DD |
| `--max-positions` | 10 | 最大持股 |
| `--n-enter` / `--n-exit` | 8 / 15 | 排名 buffer |
| `--target-vol` | 0.15 | 波动目标 |
| `--loose` | — | 关闭 strict（T 信号 T 收盘成交，乐观上界） |
| `--no-exit` | — | 禁用 L4 出场 |
| `--registry-weights` | — | 忽略 IC 权重，用 registry 默认 |
| `--sensitivity` | — | 输出参数敏感性 |
| `--out` | `$QUANT_HOME/reports/bt` | 报告目录 |

**strict 口径（默认）**：T-1 收盘因子 → T 开盘成交；与选股因子口径对齐。

**输出**：交易笔数、胜率、盈亏比、最大回撤、Sharpe/Sortino/Calmar、年化换手、出场归因等。报告写 `$QUANT_HOME/reports/bt/`。

### 6.2 其他回测/验证

| 命令 | 主要参数 | 作用 |
|---|---|---|
| `poetry run python -m scripts.backtest.validate` | `--start` `--end`（必填）`--max-positions` `--seed` | 随机 alpha 基准 + 前视泄漏检验 |
| `poetry run python -m scripts.backtest.run_intraday_timing` | `--start` `--end`（必填）`--theta` `--pool` `--horizon` `--out` | 盘中择时 θ 增益验证（日频代理） |

### 6.3 回测前置条件

- 离线库覆盖回测区间（至少 daily_raw + adj）
- ML/IC 校准建议 ≥100 样本；结论宜覆盖趋势+震荡各一轮
- 回测路径（差额 rebalance）与实盘（作战池+盘中择时）**不完全一致**，Sharpe 作参考而非实盘预期

---

## 7. 因子研究脚本

| 命令 | 主要参数 | 作用 |
|---|---|---|
| `poetry run python -m scripts.factors.build_panel` | `--start` `--end`（必填）`--out` | 构建因子面板 |
| `poetry run python -m scripts.factors.ic_report` | `--panel`（必填）`--out` | IC/ICIR 报告 → `$QUANT_HOME/reports/ic/` |
| `poetry run python -m scripts.factors.fit_weights` | `--start` `--end`（必填）`--min-icir` `--min-tstat` `--static` `--train-window` `--step` `--fdr-alpha` `--horizon` `--horizons` | 拟合 factor_weights.yml |
| `poetry run python -m scripts.research.walk_forward` | `--start` `--end`（必填）`--out` `--train-months` `--test-months` `--max-positions` | walk-forward → factor_weights_ts.yml |
| `poetry run python -m scripts.research.delist_bias_audit` | `--as-of`（必填）`--lookback-days` `--top-n` `--refresh-delisted` | 退市偏差审计 |
| `poetry run python -m scripts.research.calibrate_slippage` | `--days` | 滑点校准 |

---

## 8. 报告与落盘

全部默认 `$QUANT_HOME/reports/`：

| 子目录 | 内容 |
|---|---|
| `reports/decision/` | 日决策卡 |
| `reports/ops/` | 推送正文 |
| `reports/bt/` | 回测 |
| `reports/wf/` | walk-forward |
| `reports/ic/` | IC 报告 |

按日归档：`$QUANT_HOME/daily/{date}/raw|derived|trades|review/`。

### 纸面持仓字段

- **不再写入「战法」**
- 买入原因示例：`intraday α_z=1.12|tw=8.0%`
- 卖出原因：`exit:hard_stop` 或 `exit:atr_trailing` 等

---

## 9. 本地自检

```powershell
curl http://127.0.0.1:8085/health
curl http://127.0.0.1:8085/api/v1/quant/market/pre_market
poetry run python -m scripts.decision.daily --dry-run
poetry run python -m scripts.smoke_e2e          # 端到端冒烟（合成数据，不碰网络）
```

---

## 10. 常见问题

**Q：`ModuleNotFoundError: No module named 'pandas'`？**  
A：系统 `python` 与 Poetry 环境不是同一个。请用 `poetry run python -m ...`，或先 `.\.venv\Scripts\Activate.ps1`。用 `poetry run python -c "import sys; print(sys.executable)"` 确认解释器路径落在项目 `.venv`。

**Q：quant 报连接失败？**  
A：单次 CLI **不依赖** HTTP。若仍报错，检查是否误开了旧路径/错误环境；或设 fixture 模式。仅调度器自动跑五时段时需 `poetry run python -m app`。

**Q：面板为空 / 决策失败？**  
A：检查离线库是否已 build；至少需覆盖决策日前若干交易日。

**Q：`.env` 端口不生效？**  
A：使用 `poetry run python -m app` 启动；裸 `uvicorn` 需显式 `--port`。

**Q：回测与实盘差异？**  
A：回测用 strict 差额 rebalance；实盘 T+1 盘中 intraday_alpha 触发。见 [ARCHITECTURE.md §4.5](./ARCHITECTURE.md#45-回测-vs-实盘纸面)。

**Q：缺依赖怎么装？**  
A：`poetry install`；ML 相关再加 `--extras ml`。不要再维护平行的 `requirements.txt`。
