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
| 18:00 | `update_daily` | 每日盘后增量（spot_em + 快照，分钟级，只补当天） |
| 周五 22:00 | `maintain` | 每周离线库自愈（建库/补漏/retry-failed，重） |
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

### 5.0 执行步骤总览（从零到可回测）

> 思路：全量 build 需数小时/数天，与每日增量分 **home** 跑；build 完成后**合并 → 补缺 → 校验**。
> 三个 home：`~/.quant/offline`（离线 build）、`~/.quant/daily`（每日增量）、`~/.quant`（统一库）。
> 各脚本目录参数均指 **quant-home 根**（直接含 `store/` 的那级，见 §5.6a）。

**① 拉离线历史（到昨日）** — build 与 update 同时进行、互不干扰：

```powershell
# 离线历史（建议盘后/周末起跑；--end 钉到已收盘的昨日，避免跨交易日数据不一致）
QUANT_HOME=~/.quant/offline poetry run python -m scripts.data.build_daily --start 2021-01-01 --end 2026-08-02 --workers 1 --req-interval 5,10
```

```powershell
# 每日增量（另一 home）—— 启动 app 后调度器每日 18:00 自动跑；或手动：
QUANT_HOME=~/.quant/daily poetry run python -m scripts.data.update_daily
```

> ⚠️ **复权因子**：build_daily 在拉取循环**之后**跑 `_refresh_adj_all` 落 adj_factor。若中断在拉取阶段、没跑到复权步骤，adj_factor 会缺——validate 会报 FAIL。补法：重跑同命令（智能续传跳过已拉码、直接到复权步骤）。

**② 合并**（build 完成后）— 把离线历史 + 每日增量拼成一段连续区间：

```powershell
poetry run python -m scripts.data.merge_library --offline ~/.quant/offline --daily ~/.quant/daily --out ~/.quant
```

**③ 补缺**（合并后跑；默认可重跑且只拉仍缺市值的码；边拉边落盘可断点续传）— 补历史段 `float_mv/total_mv`（精确市值）+ `pre_close`：

```powershell
poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant
# 强制全量重拉市值（一般不需要）：
# poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant --force
```

**④ 校验** — 一键确认完整性与正确性（退出码 0=通过）：

```powershell
poetry run python -m scripts.data.validate_library --home ~/.quant
```

> 期望：`adj_factor 覆盖 ≥90%`、`index 000300·000905·000852` 齐、无重复、价格 sanity 过、
> **后复权单日跳空 0**（复权无尖刺）、`float_mv`/`pre_close` 覆盖达标。有 FAIL 先处理再往下。

**⑤ 日常运行**（此后无需再 build/merge/backfill）：

| 时机 | 动作 | 说明 |
|---|---|---|
| 每日 18:00 | `update_daily`（定时） | 当日 spot + 快照，自带 float_mv/name，无需补缺 |
| 每周五 22:00 | `maintain`（定时） | 自愈：查缺口回补 + update_daily + retry-failed |
| 每周五后（可选） | `backfill_daily_meta --home ~/.quant` | 默认只拉缺市值码，兜住新入库票；`--force` 全量重拉 |
| 每次改动后 | `validate_library --home ~/.quant` | 复验 |

**⑥ 可选·因子数据**（激活更多因子）：

```powershell
poetry run python -m scripts.data.build_fundamental_pit   # 激活 EP/BP/ROE/rev_yoy（价值因子）
poetry run python -m scripts.data.backfill_factor_snapshots --start <起> --end <止>  # 激活 flow_ratio_5
```

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

默认由调度器 **周五 22:00** 触发（每日 18:00 只跑 `update_daily` 当日增量，分钟级；重活的建库/补漏/retry-failed 拆到周五晚，避免日常 `update_daily` 被长 `build_daily` 阻塞）。

### 5.5 其他数据脚本

| 命令 | 主要参数 | 作用 |
|---|---|---|
| `poetry run python -m scripts.data.build_fundamental_pit` | `--codes` `--limit` `--batch-size` `--sleep` `--retries` `--resume` | 批量建财务 PIT |
| `poetry run python -m scripts.data.build_listing_dates` | （无 CLI 参数） | 上市日表 |
| `poetry run python -m scripts.data.backfill_factor_snapshots` | `--start` `--end`（必填） | 因子快照回填 |
| `poetry run python -m scripts.data.verify_daily` | `--sample` `--start` `--end` | 数据校验 |
| `poetry run python -m scripts.data.audit_data_health` | `--probe-code` | 健康审计 |
| `poetry run python -m scripts.data.merge_library` | `--offline` `--daily` `--out` | 合并离线库与每日增量 |
| `poetry run python -m scripts.data.backfill_daily_meta` | `--home` `--codes` `--workers` `--req-interval` `--force` `--flush-every` | 补历史段 float_mv/total_mv/pre_close（默认只拉缺市值码；边拉边落盘可续传） |
| `poetry run python -m scripts.data.validate_library` | `--home` `--start` `--end` | 离线库完整性与正确性校验（含复权连续性） |

### 5.6a 离线库与每日增量合并（`merge_library`）

**场景**：全量 `build_daily` 需数小时/数天，期间不想让日常 `update_daily` 与它抢同一 store。

**目录级别（重要）**：`--offline` / `--daily` / `--out`（以及校验脚本的 `--home`）都指向
**quant-home 根**——即**直接包含 `store/` 子目录**的那一级，不是 `store/` 本身、也不是 `data/`：

```text
~/.quant/offline/            ← --offline 指到这级（含 store/）
├── store/                   # daily_raw / adj_factor / index_daily / calendar.parquet / 快照…
└── data/                    # build_failed.jsonl、no_bar_dates.json、no_mv_dates.json 等
~/.quant/daily/              ← --daily 指到这级
~/.quant/                    ← --out / --home 指到这级
```

```powershell
# 1. 离线 build 到昨日（home 分开）
QUANT_HOME=~/.quant/offline poetry run python -m scripts.data.build_daily --start 2021-01-01 --end 2026-08-02 --workers 1 --req-interval 5,10
# 2. 每日盘后增量（另一 home）
QUANT_HOME=~/.quant/daily poetry run python -m scripts.data.update_daily
# 3. build 完成后合并到统一 home
poetry run python -m scripts.data.merge_library --offline ~/.quant/offline --daily ~/.quant/daily --out ~/.quant
# 4. 补历史段缺列（float_mv/total_mv 精确市值 + pre_close；默认只拉仍缺市值的码，可重跑）
poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant
#    强制全量重拉：加 --force
# 5. 校验合并结果
poetry run python -m scripts.data.validate_library --home ~/.quant
```

**`backfill_daily_meta`**：用 `stock_value_em`（东财估值分析，逐日历史）把历史段 `float_mv/total_mv`
**精确**补上、`pre_close` 用 `close[t-1]` 推导。参数 `--home`（quant-home 根）/ `--codes`
（只补指定码）/ `--workers` / `--req-interval` / `--force`（默认只拉仍缺市值的码；`--force`
才全量重拉）/ `--flush-every`（默认 50：每成功 N 只落盘，中断后重跑只补未落盘缺码）。
写回对已有非空值 `fillna` 不覆盖。退市等源无市值的码记入 `data/backfill_mv_unavailable.json`，
下次默认跳过（`--force` 重试）。**成功拉过一次后仍缺的日期**记入 `data/no_mv_dates.json`
（结构同 `no_bar_dates.json`，语义是「源无市值」而非「源无 K 线」，**禁止混用**），下次不再为这些日重拉；
新入库且未豁免的缺日仍会触发再拉。运行时会打印计划（总共/已齐·豁免跳过/源无跳过/待执行）
与周期性进度（ok/fail/unavail/flushed/速率/ETA），失败码带原因。**name 刻意不灌历史**（当前名灌历史 = ST 过滤前视），PIT 名靠 `update_daily` 的
`name_snapshot` 逐日积累；确需当前名兜底用 `--fill-name`。日常增量不需要补（spot 当天自带这些列）；
每周五 `maintain` 后可选重跑兜住新入库票。

**build_daily 与 update_daily 的 daily_raw 列差异及影响**：

| 列 | build（`stock_zh_a_hist`） | update（spot_em） | 缺失影响 |
|---|---|---|---|
| `name` | ❌ 无 | ✅ | ST 过滤走 `name_snapshot`（update 每日落），**无影响**（无快照则 ST 过滤退化，属既有限制） |
| `pre_close` | ❌ 无 | ✅ | 回测 `prev_close` 由序列前一日 close 现算，**基本无影响** |
| `float_mv` | ❌ 无 | ✅ | **有影响**：`neutralize.py` 用 `log(float_mv)` 做市值中性化，全缺则 `use_size=0` → **跳过市值中性化**，因子保留 size 暴露。可选近似：`当前流通股本 × 后复权 close` 生成代理 float_mv |
| `total_mv` | ❌ 无 | ✅ | 极少直接用，无影响 |

合并脚本把 build 行缺的列补 NaN 后归一到统一 13 列，所以**合并后行为 = 纯 build 库行为**，
合并本身不放大缺失。

一致性保证：

- **daily_raw 归一到 13 列**（`DAILY_RAW_COLUMNS`）：统一 schema 后按 `(code, date)` 去重、
  **update 覆盖 build**（重叠日以增量为准）。
- **复权不复发除权尖刺 bug**：合并只做 raw + `adj_factor` 的并集去重，**不自己算复权**；
  读时经 `quant/data/adjust.py:apply_hfq`（merge_asof 还原累积因子）得连续后复权价。
- `index_daily` / `calendar` / snapshot 目录（universe/industry/name_snapshot/
  fundamental_pit/因子快照等）取并集，snapshot 冲突 daily 优先。

**注意**：`merge_library` 不补 adj_factor——若 build 中断未跑 `_refresh_adj_all`，须先补复权因子
（重跑 build_daily 到 `_refresh_adj_all` 阶段，或 `refresh_adj_for_codes`），否则 `validate_library`
会报 adj 覆盖 FAIL、回测除权假跳空。

### 5.6b 完整性与正确性校验（`validate_library`）

```powershell
poetry run python -m scripts.data.validate_library            # 校验当前 QUANT_HOME
poetry run python -m scripts.data.validate_library --home ~/.quant
```

**`--home` 级别**：同 merge——**quant-home 根**（直接包含 `store/` 的那一级，见 §5.6a 目录树）。
所有检查（daily_raw / adj_factor / index_daily / calendar / no_bar 豁免）都从该目录读，
不读当前 QUANT_HOME。

跑一遍即知数据是否完整、是否正确（退出码 0=通过，1=发现问题）：

- **[1] 基础**：calendar 天数 / daily_raw 行·码·日期区间 / name 非空率
- **[2] 完整性**：`adj_factor` 覆盖（<90% FAIL）/ `index_daily` 000300·000905·000852（缺 FAIL）/
  per-code 日历覆盖（低 WARN，次新/停牌为合法缺日）/ `float_mv` 市值覆盖（<50% WARN，可跑
  `backfill_daily_meta`）/ `pre_close` 覆盖（<90% WARN）
- **[3] 正确性**：重复 `(code,date)`（>0 FAIL）/ 价格 sanity（close>0、high>=low、high·low 夹住
  open·close、volume·amount>=0，违规 FAIL）/ **后复权单日跳空 >28%**（>0 FAIL——`apply_hfq`
  精确 join 旧 bug 的表现，A 股涨停/跌停 ≤20% 故阈值安全）

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
