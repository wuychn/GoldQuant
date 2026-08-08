# 运维与操作指南

> 依赖由 **Poetry** 管理。下文命令一律在项目根目录执行，前缀用 `poetry run python -m ...`。  
> **数据完备后的每日选股与模拟交易**见 [DAILY_OPS.md](./DAILY_OPS.md)。配置项细节见 [CONFIG.md](./CONFIG.md)。

---

## 0. 新手怎么读这份文档

按时间顺序做即可，不必一次读完：

| 阶段 | 章节 | 你在做什么 |
|---|---|---|
| 装环境 | §1～§2 | 装依赖、指定数据目录 |
| 第一次建库 | §3 | 拉历史行情 →（可选分流合并）→ 补市值 → 校验；脏数据再清理 |
| 可选加强 | §4 | 财务/主题因子等，不做也能先回测和选股 |
| 验证策略 | §5 | 历史回测，看系统大致能不能赚钱（参考值） |
| 日常养库 | §6 | 每天增量、每周自愈 |
| 无人值守 | §7 | 开 API + 定时任务，自动增量/选股/盘中模拟 |
| 研究调参 | §8 | 因子权重、walk-forward（可选） |
| 查问题 | §9～§11 | 报告在哪、复权约定、FAQ |

### 0.1 最短路径清单（第一次跑通）

在项目根 `D:\workspace\GoldQuant` 下逐项打勾：

1. [ ] `poetry install`，复制并编辑 `.env`（至少填 `GOLDQUANT_QUANT_HOME_DIR`）
2. [ ] （推荐）分目录建库：`build_daily` → 并行 `update_daily` → `merge_library`
3. [ ] `backfill_daily_meta` 补市值 / 昨收
4. [ ] `validate_library` 退出码 0（允许 WARN）
5. [ ] 若价格 sanity FAIL → `scrub_invalid_bars --dry-run` → `--apply` → 再 validate
6. [ ] （可选）跑一段 `backtest.run` 确认能出报告
7. [ ] 日常：`update_daily`；生产：`poetry run python -m app` 开调度
8. [ ] 选股与模拟交易 → [DAILY_OPS.md](./DAILY_OPS.md)

### 0.2 名词速查

| 名词 | 白话 |
|---|---|
| **quant-home / `--home`** | 数据根目录，下面有 `store/`、`reports/`、`paper_account/` 等。读写库的脚本都可用 `--home` 指定；不设则读环境变量或默认 `~/.quant` |
| **store** | `$QUANT_HOME` 下真正存 Parquet 行情的地方（如 `daily_raw`、`adj_factor`） |
| **daily_raw** | 不复权 OHLCV 日线（开高低收、成交量等） |
| **adj_factor** | 后复权因子；因子/回测必须用复权价，否则除权日会假跳空 |
| **float_mv / pre_close** | 流通市值、昨收；选股过滤与部分因子要用 |
| **universe** | 当日可交易股票池快照 |
| **PIT** | Point-in-Time，只用当时已知信息，避免用未来财报「穿越」 |
| **offline / daily 分流** | 全量建库慢，另开两个目录分别跑历史与每日增量，最后合并进正式库 |
| **作战池 battle_pool** | T 晚选出的次日可买候选列表 |
| **sell_watch** | T 晚写好的持仓卖出监控（止损等），供 T+1 盘中用 |
| **纸面账户 paper_account** | 模拟成交账户，与人工 `state/` 隔离 |

下文示例正式库：`D:\ProgramData\.quant`；建库分流：`...\offline`、`...\daily`。

---

## 1. 环境与安装（必须）

**用途**：安装 Python 依赖，让 `poetry run python -m ...` 能跑起来。  
**必须性**：首次搭建必须。  
**你会得到**：项目下的 `.venv`，以及可编辑的 `.env`。

### 1.1 要求

- Python **3.11+**（推荐 3.11；约束 `>=3.11,<3.14`）
- [Poetry](https://python-poetry.org/) 2.x
- 可访问外网（拉 A 股行情）
- Windows / Linux 均可

### 1.2 安装步骤

```powershell
cd D:\workspace\GoldQuant

# 安装主依赖（Poetry 会创建/使用项目 .venv）
poetry install

# 做 IC/拟合/walk-forward 等 ML 研究时再装可选组
poetry install --extras ml

# 从模板生成环境文件
copy .env.example .env
```

然后用编辑器打开 `.env`，至少改这一项（路径按你的机器）：

```env
GOLDQUANT_QUANT_HOME_DIR=D:\ProgramData\.quant
```

飞书推送、LLM 新闻等按 [CONFIG.md](./CONFIG.md) 填写；只建库/回测可以先不配飞书。

**等价写法**：激活虚拟环境后可直接用 `python`：

```powershell
.\.venv\Scripts\Activate.ps1
python -m scripts.data.validate_library --home D:\ProgramData\.quant
```

与 `poetry run python -m ...` 是同一解释器。确认命令：

```powershell
poetry run python -c "import sys; print(sys.executable)"
```

应指向项目下的 `.venv\Scripts\python.exe`。

---

## 2. 配置数据目录（必须）

**用途**：告诉系统「行情和报告写到哪里」，避免默认写到用户目录 `~/.quant`，也避免建库时和日常增量互相踩文件。  
**必须性**：首次必须。调度器、不带 `--home` 的进程都读这里。  
**你会得到**：一个（或三个）空的 quant-home 目录结构，之后脚本会自动创建 `store/` 等子目录。

### 2.1 推荐目录布局

```text
D:\ProgramData\.quant\           ← 正式库（合并后日常只用这个）
D:\ProgramData\.quant\offline\   ← 首次全量 build_daily 写入（可选分流）
D:\ProgramData\.quant\daily\     ← 建库期间每天 update_daily 写入（可选分流）
```

正式库就绪后，`store/` 里常见内容：

| 路径 | 存什么 | 谁依赖它 |
|---|---|---|
| `daily_raw/` | 不复权日线 | 一切行情相关 |
| `adj_factor/` | 后复权因子 | 因子、回测、出场 |
| `universe/` | 每日股票池 | 选股 |
| `industry/` | 行业 PIT | 组合行业约束 |
| `fundamental_pit/` | 财务 PIT | 价值因子（可选补强） |
| `index_daily/` | 指数日线 | 基准、择时 |
| `calendar.parquet` | 交易日历 | 缺日检测、回测日期 |

### 2.2 三种指定方式（优先级从高到低）

| 方式 | 示例 | 适用 |
|---|---|---|
| CLI `--home` | `--home D:\ProgramData\.quant` | 手动跑脚本、多库并存 |
| `.env` | `GOLDQUANT_QUANT_HOME_DIR=D:\ProgramData\.quant` | **生产推荐**（调度继承） |
| 环境变量 | `$env:QUANT_HOME = "D:\ProgramData\.quant"` | 临时覆盖当前终端 |

解析实现见 `quant/store/paths.py`。读写 `store` / `reports` / `paper_account` 的脚本一般都支持 `--home`；`merge_library` 用三个路径参数代替（见 §3.3）。

---

## 3. 从零搭建离线库（首次必须）

**这一节在干什么**：把「能选股、能回测」所需的历史日线库准备好。全量拉取可能要数小时～数天，所以推荐历史与每日增量**分目录**跑，建完再合并。

```text
offline (build_daily 拉历史)  ──┐
                               ├── merge_library → 正式 home
daily   (update_daily 每日)  ──┘         ↓
                                   backfill_daily_meta（补市值）
                                         ↓
                                   validate_library（校验）
                                         ↓
                              若 FAIL → scrub_invalid_bars → 再校验
```

若你愿意建库期间**不跑**每日增量，也可以只用一个正式目录跑 `build_daily`，跳过 §3.2～§3.3，直接 §3.4。

---

### 3.1 拉离线历史 `build_daily`（首次必须）

**用途**：从行情源按日期区间拉取全 A 股日线（不复权 OHLCV）、主要指数、交易日历，并在拉取结束后初始化后复权因子等，写入指定 quant-home。这是离线库的「主体数据」。  
**必须性**：首次建库必须。同一命令可反复执行（智能断点续传，已齐全的股票会跳过）。  
**前置**：§1～§2 完成；外网可用；建议 `--end` 设为**已收盘的最近交易日**（不要跨到未收盘的今天，避免半日数据）。  
**后续**：若用了分流 → §3.3 合并；否则 → §3.4 补缺。  
**你会得到**：`--home` 下 `store/daily_raw/`、`adj_factor/`、`index_daily/`、`calendar` 等；进度日志里会看到已拉码数/失败清单。

```powershell
poetry run python -m scripts.data.build_daily `
  --home D:\ProgramData\.quant\offline `
  --start 2021-01-01 --end 2026-08-07 `
  --workers 1 --req-interval 5,10
```

| 参数 | 默认 | 推荐 | 含义 |
|---|---|---|---|
| `--home` | 当前 QUANT_HOME | 建库用 `...\offline` | quant-home 根（含 `store/`） |
| `--start` | `2021-01-01` | `2021-01-01` | 建库/完整性检查起始日 |
| `--end` | 今天 | 已收盘昨日 | 结束日 |
| `--workers` | `3` | `1`（避东财限流） | 并发线程数；过高易被封 |
| `--req-interval` | `1,3` | `5,10` | 请求间隔秒，格式 `MIN,MAX` 或单值 `N` |
| `--limit` | — | 仅调试 | 只拉前 N 只（试跑通流程） |
| `--codes` | — | 仅调试 | 逗号分隔代码，只拉这些票 |
| `--ignore-existing` | off | 补某段缺口时开 | 跳过「已齐全则跳过」检查，强制再拉；并跳过市场缺口第二轮 |
| `--no-gap-fill` | off | 一般不开 | 不做「全市场缺某天」的第二轮补拉 |
| `--no-delisted` | off | 一般不开 | 不并入退市股（默认并入，减轻幸存者偏差） |
| `--no-adj` | off | **不要开** | 跳过复权因子；会导致回测除权假跳空 |
| `--retry-failed` | off | maintain/补救时开 | 只重试 `build_failed.jsonl` 里的失败代码 |
| `--dead-threshold` | `5` | `5` | 同一代码失败次数达此值标为 dead，不再自动重试 |

**续传怎么理解**：相对 `[start,end]`，某只股票缺任意一天就再拉；齐全则跳过。停牌导致源站无 K 线的交易日会记入 `no_bar_dates.json`，避免反复空打。复权步骤在全部拉取循环之后；若中断在拉取阶段、adj 还没生成，**再跑同一条命令**即可续上并补复权。

只补某几天（跳过智能扫描）：

```powershell
poetry run python -m scripts.data.build_daily `
  --home D:\ProgramData\.quant\offline `
  --start 2026-07-20 --end 2026-07-25 `
  --ignore-existing --workers 1 --req-interval 5,10
```

| 常见告警 | 原因 | 处理 |
|---|---|---|
| `ProxyError` / 连不上代理 | Clash 等注入了系统代理 | `.env` 保持 `GOLDQUANT_PROXY_ENABLED=false` |
| parquet 读损坏 | 历史并发写坏过分区 | 损坏文件会改名 `.corrupt.*`；重跑同命令续传即可 |

---

### 3.2 建库期间每日增量 `update_daily`（强烈建议）

**用途**：在**每个交易日收盘后**，用东财 spot 等接口抓「当天」全市场行情与快照（指数、行业、universe、资金流/热度等因子快照），追加进库。spot 自带流通市值与股票名称，日常不必再为当天跑 backfill。  
**必须性**：建库拖很久时强烈建议另开 `...\daily` 每天跑，避免合并后正式库缺最近几天；正式库启用后变为**每日必须**（见 §6.1）。  
**前置**：可与 §3.1 并行（不同 `--home`）。非交易日会自动跳过。  
**后续**：建库结束后与 offline 一并 §3.3 合并。  
**你会得到**：当日 `daily_raw` 一行/每股、相关快照文件；无效盘口（`close<=0`、OHLC 不自洽）会被丢弃不落库。

```powershell
# 建库期间：写到 daily 分流目录
poetry run python -m scripts.data.update_daily --home D:\ProgramData\.quant\daily

# 指定某一天补跑
poetry run python -m scripts.data.update_daily --home D:\ProgramData\.quant\daily --date 2026-08-07
```

| 参数 | 默认 | 推荐 | 含义 |
|---|---|---|---|
| `--home` | 当前 QUANT_HOME | 建库期 `...\daily`；日常正式库 | quant-home 根 |
| `--date` | 今天 | 一般不设 | 交易日 YYYY-MM-DD；非交易日跳过 |
| `--force-fundamental-pit` | off | 披露季外要强刷财务时开 | 无视披露窗口，增量刷新 `fundamental_pit` |
| `--fund-flow-mode` | `rank` | `rank` | `rank`=分页为主、失败后逐票补缺；`per_symbol`=强制旧逐票 |
| `--fund-flow-page-size` | yml `100` | `100` | rank 每页条数 |
| `--req-page-interval` | yml `61,121` | `61,121` | 页间间隔秒，`MIN,MAX` 或 `N`（与 yml `req_page_interval` 同名；下限 10） |
| `--req-symbol-interval` | yml `5,10` | `5,10` | 逐票间隔秒（yml `req_symbol_interval`） |
| `--req-batch-pause` | yml `120,240` | 一般不设 | 批间/失败跳页暂停（yml `req_batch_pause`） |
| `--req-burst-pages` | yml `1,3` | 一般不设 | 每成功 N 页批停（yml `req_burst_pages`） |
| `--flush-every` | `50` | `50` | 仅 `per_symbol`：每 N 只落盘 |

示例：

```powershell
poetry run python -m scripts.data.update_daily `
  --req-page-interval 61,121 `
  --req-symbol-interval 5,10 `
  --req-batch-pause 120,240 `
  --req-burst-pages 1,3
```

**资金流（默认 rank）**：编排在 `quant/data/fund_flow_5d.py`——先
`market.fetch_stock_fund_flow_rank`（yml 接口级换源；页间 `req_page_interval`；
逐票 `req_symbol_interval`；每 `req_burst_pages` 页批停 `req_batch_pause`；
单页失败跳过，**连续失败 2 次即结束分页改逐票**），分页结束后对缺码走
`enrich.fetch_stock_fund_flow_daily` 逐票（跳过已有码；断连不记完成）。
断点：`$QUANT_HOME/data/fund_flow_rank_progress/{date}/5日/`（pages + meta）与同目录
`per_symbol.json` / `per_symbol_values.json`。日志前缀 `[fund_flow_5d]` / `[fund_flow_rank]`。
口径对比（默认 `D:\ProgramData\.quant_tmp`）：`poetry run python -m scripts.data.compare_fund_flow_rank`。

**资金流（per_symbol）**：进度在 `$QUANT_HOME/data/fund_flow_progress/{date}.json`。

**注意**：spot 没有「补历史某一天盘口」的能力——当天没抓成功，只能靠告警重跑当天，无法事后完美还原。

---

### 3.3 合并 `merge_library`（用了分流则必须）

**用途**：把「离线历史库」和「每日增量库」拼成一份时间连续的统一库，供校验、回测、日常决策使用。  
**必须性**：若 §3.1 / §3.2 用了两个不同 home，则必须执行；若始终写在同一个正式目录，可跳过本节。  
**前置**：offline 的 `build_daily` 已跑完（或足够覆盖）；daily 侧至少有近期增量更佳。  
**后续**：把 `.env` 的 `GOLDQUANT_QUANT_HOME_DIR` 指到 `--out`，再跑 §3.4。  
**你会得到**：`--out` 下完整的 `store/`（历史 + 增量拼好）。  
**参数说明**：本脚本**没有** `--home`，而是三个都是 quant-home 根（均应直接含或将生成 `store/`）。

```powershell
poetry run python -m scripts.data.merge_library `
  --offline D:\ProgramData\.quant\offline `
  --daily D:\ProgramData\.quant\daily `
  --out D:\ProgramData\.quant
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--offline` | 必填 | 离线库根（`build_daily` 写入处） |
| `--daily` | 必填 | 增量库根（`update_daily` 写入处） |
| `--out` | 必填 | 合并输出的正式库根 |

合并后请确认：

```env
GOLDQUANT_QUANT_HOME_DIR=D:\ProgramData\.quant
```

---

### 3.4 补缺 `backfill_daily_meta`（必须）

**用途**：给历史日线补上更精确的流通/总市值（`float_mv`/`total_mv`）和昨收 `pre_close`。`build_daily` 历史段往往缺这些字段；选股过滤与部分因子需要它们。默认只拉「仍缺市值」的股票，边拉边落盘，可安全重跑。  
**必须性**：合并后（或单库 `build_daily` 完成后）至少跑一次。  
**前置**：正式库已有 `daily_raw`。  
**后续**：§3.5 校验。  
**你会得到**：更多行带上市值/昨收；日志里有进度与 ETA。

```powershell
poetry run python -m scripts.data.backfill_daily_meta --home D:\ProgramData\.quant

# 强制全市场重拉市值（慢，一般不需要）
# poetry run python -m scripts.data.backfill_daily_meta --home D:\ProgramData\.quant --force
```

| 参数 | 默认 | 推荐 | 含义 |
|---|---|---|---|
| `--home` | 当前 QUANT_HOME | 正式库 | quant-home 根 |
| `--codes` | 库内全部候选 | 一般不设 | 逗号分隔，只补这些码 |
| `--workers` | `1` | `1`～`2` | 并发线程 |
| `--req-interval` | `0,2` | 默认即可 | 请求间隔秒 |
| `--force` | off | 一般不开 | 忽略「已有市值」判断，全量重拉 |
| `--flush-every` | 脚本默认（常见 20） | 默认 | 每处理 N 只写盘一次，便于断点续传 |
| `--fill-name` | off | **不要开**（除非你清楚风险） | 用「当前股票名」灌历史行，会污染 ST 历史过滤（前视） |

---

### 3.5 校验 `validate_library`（必须）

**用途**：对正式库做「能不能放心用来回测/选股」的体检：行数与股票数、复权覆盖、指数是否齐全、有无重复键、价格是否合理、后复权是否出现异常尖刺跳空等。  
**必须性**：§3.4 之后必须；以后大改数据也建议再跑。  
**前置**：指向要检查的正式 `--home`。  
**后续**：退出码 0 可进日常/回测；有 FAIL 先按提示处理（价格问题见 §3.6）。  
**你会得到**：终端分段报告（含 **实际数据起止**、跨度内市场级缺日完整列表、末日之后未入库交易日；个股覆盖已排除停牌等 `no_bar`）；`0`=通过（可以有 WARN），`1`=存在 FAIL。

```powershell
poetry run python -m scripts.data.validate_library --home D:\ProgramData\.quant
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | 要检查的 quant-home |
| `--start` | `2000-01-01` | 检查窗口起点（一般不用改） |
| `--end` | `2099-12-31` | 检查窗口终点 |

**怎样算过关（期望）**：

- `adj_factor` 覆盖足够高（过低会 FAIL）
- 指数日线 `000300` / `000905` / `000852` 都有
- 无重复 `(code, date)`
- 价格 sanity：`close>0`、high/low 夹住 open/close、量额非负
- 后复权相邻交易日异常跳空为 0（停牌复牌/次新大波动已豁免）
- `float_mv` / `pre_close` 覆盖达标（过低会 WARN，可再跑 backfill）

WARN 里常见「某几只股票日历覆盖偏低」——次新/长期停牌往往合法，可查可不查。

---

### 3.6 清理脏盘口 `scrub_invalid_bars`（数据不对时必须）

**用途**：从已入库的 `daily_raw` 里删掉无效 K 线行（例如停牌被写成全 0、`close<=0`、最高价夹不住开收盘等）。新数据在 `update_daily` 入口已拒写；本脚本专门清理**历史脏行**。  
**必须性**：仅当 `validate_library` 报「价格 sanity FAIL」时必须处理；平时不必跑。  
**前置**：先 `--dry-run` 看清会删哪些；确认后再 `--apply`。  
**后续**：再跑一遍 `validate_library`。  
**你会得到**：dry-run 打印样本行；apply 后按年分区重写，行数减少。

```powershell
# 1）只看不删
poetry run python -m scripts.data.scrub_invalid_bars --home D:\ProgramData\.quant --dry-run

# 2）确认无误后真正删除
poetry run python -m scripts.data.scrub_invalid_bars --home D:\ProgramData\.quant --apply

# 3）复验
poetry run python -m scripts.data.validate_library --home D:\ProgramData\.quant
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |
| `--dry-run` | 默认即 dry-run（未传 `--apply` 时） | 只统计、打印样本，不写盘 |
| `--apply` | off | 真正按年分区重写并删除脏行（不可与「只想看」同时当作互斥目标乱用） |

---

## 4. 可选数据补强

§3 跑通后，已经可以回测和日决策。下面这些用来**激活更多因子**或**排查问题**，不阻塞主路径。

---

### 4.1 财务 PIT `build_fundamental_pit`（可选）

**用途**：按公告日构建财务 Point-in-Time 库（利润、净资产等），从而激活价值类日频因子（如 EP、BP、ROE、收入同比等）。没有它，这些因子算不出来或为空。  
**必须性**：要用价值因子时建议跑；只做价量动量可暂缓。  
**前置**：正式库已有股票列表/universe。耗时较长，建议加 `--resume` 断点续跑。  
**后续**：无需立刻 validate；日决策/面板构建时会自动读到。  
**你会得到**：`store/fundamental_pit/` 下按代码或分区的财务 PIT 数据。

```powershell
poetry run python -m scripts.data.build_fundamental_pit --home D:\ProgramData\.quant --resume
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |
| `--codes` | universe 全量 | 逗号分隔，只拉指定码 |
| `--limit` | `0`（不限） | 最多拉取只数（调试可设小） |
| `--batch-size` | `50` | 批量写相关兼容参数 |
| `--sleep` | `0.3` | 单只请求间隔秒，防限流 |
| `--retries` | `3` | 单只失败重试次数 |
| `--resume` | off | **推荐打开**：跳过库中已有代码，续跑 |

---

### 4.2 主题等因子快照回填 `backfill_factor_snapshots`（可选）

**用途**：按交易日回填历史「主题/概念动量」等因子 PIT 快照（例如 theme 相关），让历史回测区间里也能用到这些因子。基本面请用 §4.1，不要混用本脚本。  
**必须性**：回测/研究要用 theme 等快照因子时再跑。  
**前置**：正式库日历与日线已就绪；区间勿过大一次打爆接口。  
**后续**：构建面板或回测时自动可用。  
**你会得到**：对应日期的因子快照文件落在 store 约定目录。

```powershell
poetry run python -m scripts.data.backfill_factor_snapshots `
  --home D:\ProgramData\.quant --start 2024-01-01 --end 2026-08-07
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |
| `--start` | 必填 | 回填起始日（含） |
| `--end` | 必填 | 回填结束日（含） |

---

### 4.3 上市日表 `build_listing_dates`（可选）

**用途**：拉取/整理每只股票的上市日期，写入库中。`build_daily` 做「完整性期望窗口」时会参考上市日（次新股不应要求上市前也有 K 线），减少误报缺日。  
**必须性**：可选；缺日 WARN 很多或频繁全量补漏时值得跑。  
**前置**：正式 home 可写。  
**后续**：之后再跑 `build_daily` / `maintain` 时生效。  
**你会得到**：上市日相关表/文件（供完整性检查使用）。

```powershell
poetry run python -m scripts.data.build_listing_dates --home D:\ProgramData\.quant
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |

---

### 4.4 抽样核对 `verify_daily`（可选排查）

**用途**：随机抽 N 只股票，核对本地日线与源站是否大致一致，用于怀疑「某段数据不对」时的抽查，**不能替代** `validate_library`。  
**必须性**：可选。  
**前置**：库非空。  
**你会得到**：抽样对比日志（一致/偏差提示）。

```powershell
poetry run python -m scripts.data.verify_daily --home D:\ProgramData\.quant --sample 20
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |
| `--sample` | `20` | 随机抽样只数 |
| `--start` | 不限 | 校验日期下限 |
| `--end` | 不限 | 校验日期上限 |

---

### 4.5 健康审计 `audit_data_health`（可选排查）

**用途**：检查库的整体健康度，并可用一只股票探测 akshare 等源站字段/名称列是否变化（源站改版时排查）。  
**必须性**：可选；接口报错或列对不上时使用。  
**前置**：网络可用。  
**你会得到**：健康报告与探测结果。

```powershell
poetry run python -m scripts.data.audit_data_health --home D:\ProgramData\.quant --probe-code 000001
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |
| `--probe-code` | `000001` | 用于探测源站历史行情名称列的股票代码 |

---

## 5. 回测（库就绪后：研究时建议跑）

**这一节在干什么**：用历史数据模拟「按当前选股规则持仓」的绩效，检查因子/组合是否离谱。  
**前置**：正式库覆盖回测区间（至少 `daily_raw` + `adj`）；建议 `validate_library` 已通过。  
**注意**：默认是 **strict** 口径（T-1 收盘算因子 → T 开盘成交），与实盘「作战池 + T+1 盘中择时」不完全相同；Sharpe 等指标作参考，不是实盘预期。详见 [ARCHITECTURE.md §4.5](./ARCHITECTURE.md#45-回测-vs-实盘纸面)。

---

### 5.1 主回测 `backtest.run`（研究时建议）

**用途**：按官方日频选股/组合/出场规则，在指定区间跑完整回测，并导出绩效指标与报告。  
**必须性**：改策略或想确认系统可用性时建议跑；不是每日运维必须。  
**前置**：§3 完成。区间建议覆盖趋势与震荡各一段。  
**后续**：报告在 `--out`；不满意再调参或跑 §8 研究脚本。  
**你会得到**：`$QUANT_HOME/reports/bt/` 下报告；终端打印交易笔数、胜率、回撤、Sharpe/Sortino/Calmar、换手、出场归因等。

```powershell
poetry run python -m scripts.backtest.run `
  --home D:\ProgramData\.quant --start 2024-01-01 --end 2026-08-07 `
  --workers 4
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | 读库与默认报告根 |
| `--start` / `--end` | 必填 | 回测区间（含） |
| `--max-positions` | `10` | 最大持股只数 |
| `--n-enter` / `--n-exit` | `8` / `15` | 纳入/剔除的排名 buffer（减轻换手抖动） |
| `--target-vol` | `0.15` | 目标年化波动，用于仓位缩放 |
| `--loose` | off | 关闭 strict（变成更乐观的成交假设，非官方口径） |
| `--no-exit` | off | 关掉 L4 出场规则，只看选股 alpha |
| `--registry-weights` | off | 忽略 IC/校准权重，改用 registry 默认权重 |
| `--sensitivity` | off | 额外跑参数敏感性扫描（更慢） |
| `--workers` | `1` | 因子面板按**股票**并行进程数；日循环撮合仍串行。结果应与 `1` 一致；CPU 多且内存够可用 `2`–`4`，内存紧张保持 `1` |
| `--sens-workers` | `1` | 敏感性各参数点并行进程数（仅 `--sensitivity`）；默认 `1` |
| `--out` | `$QUANT_HOME/reports/bt` | 报告输出目录 |

并行只加速「建 alpha（按票算因子）」与可选的敏感性多组回测，**不**并行逐日撮合；默认 `workers=1` 与旧口径一致。

---

### 5.2 随机基准与泄漏检验 `backtest.validate`（可选）

**用途**：用随机 alpha 做对照，并做简单的前视泄漏检验，判断回测框架是否「虚高」。  
**必须性**：可选；改引擎或怀疑结果造假时跑。  
**前置**：同主回测。  
**你会得到**：终端检验结论（随机基准表现 vs 真实规则量级等）。

```powershell
poetry run python -m scripts.backtest.validate `
  --home D:\ProgramData\.quant --start 2024-01-01 --end 2024-06-30
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |
| `--start` / `--end` | 必填 | 检验区间 |
| `--max-positions` | `10` | 最大持股 |
| `--seed` | `42` | 随机 alpha 的随机种子（可复现） |

---

### 5.3 盘中择时增益 `run_intraday_timing`（可选）

**用途**：用日频数据代理验证「盘中 θ 择时」相对直接持有作战池是否有增益（不是完整 tick 回测）。  
**必须性**：可选；调盘中阈值 θ 时参考。  
**前置**：库覆盖区间。  
**你会得到**：JSON 报告（默认在 `$QUANT_HOME/reports/intraday_timing/`）。

```powershell
poetry run python -m scripts.backtest.run_intraday_timing `
  --home D:\ProgramData\.quant --start 2024-01-01 --end 2024-06-30
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |
| `--start` / `--end` | 必填 | 区间 |
| `--theta` | `1.0` | 盘中择时触发阈值；越低触发越多 |
| `--pool` | `30` | 每日作战池规模 |
| `--horizon` | `5` | 持有期评估窗口（交易日） |
| `--out` | 自动路径 | JSON 报告路径 |

---

## 6. 日常数据维护（库就绪后必须）

正式库通过校验后，**不要天天全量 build**。日常只做增量；每周做一次自愈。

---

### 6.1 每日增量 `update_daily`（必须）

**用途**：每个交易日盘后，把「今天」的行情与快照写入正式库，供当晚选股使用。  
**必须性**：**日常必须**。漏跑则 `daily_decision` 可能缺少当日数据。  
**时机**：调度默认 **18:00**；也可手动。  
**前置**：`.env` 已指向正式库，或命令带 `--home`。  
**后续**：当晚选股见 [DAILY_OPS.md](./DAILY_OPS.md)。  
**参数**：与 §3.2 相同。

```powershell
poetry run python -m scripts.data.update_daily --home D:\ProgramData\.quant
```

生产环境更推荐在 `.env` 写死 `GOLDQUANT_QUANT_HOME_DIR`，调度子进程自动继承，命令里可不写 `--home`。

---

### 6.2 每周自愈 `maintain`（强烈建议）

**用途**：自动检查离线库是否空、是否缺交易日，必要时触发 `build_daily` 建库/补漏，再跑当天 `update_daily`，并重试历史失败清单。避免「静默缺几天数据」拖垮后续选股。  
**必须性**：强烈建议每周跑；调度默认 **周五 22:00**。  
**时机**：避开白天，以免长 `build_daily` 堵住日常增量。  
**前置**：正式 `--home`；子进程会自动带上同一 `--home`。  
**后续**：可选再跑 `backfill_daily_meta`（只补新票缺市值）；有怀疑则 `validate_library`。  
**你会得到**：缺口被回补、失败码重试、当日增量更新；日志里分步打印子任务。

```powershell
poetry run python -m scripts.data.maintain --home D:\ProgramData\.quant

# 指定维护截至日与空库时的历史起点
poetry run python -m scripts.data.maintain `
  --home D:\ProgramData\.quant --date 2026-08-07 --start 2021-01-01
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | 会转发给子进程 `build_daily` / `update_daily` |
| `--date` | 今天 | 维护截至日；非交易日回退到最近交易日 |
| `--start` | `2021-01-01` | 若库为空，全量建库的起始日 |

**内部顺序**（理解即可，一般不用手拆）：

1. 库空 → 全量 `build_daily`  
2. 有库 → 对照交易日历找缺口 → `build_daily --ignore-existing` 回补  
3. `update_daily` 当日增量  
4. `build_daily --retry-failed` 重试失败清单  

---

## 7. 启动定时任务（要无人值守则必须）

**用途**：启动 FastAPI 应用；若 `scheduler.enabled: true`，会按 `quant.yml` 自动注册「新闻 / 盘前 / 盘中买卖 / 午间复盘 / 每日增量 / 晚间选股 / 周五维护」等任务。  
**必须性**：要 7×24 自动跑则必须；**单次**手动 `poetry run python -m quant <mode>` **不必**先开 API。  
**前置**：§2 的 home 已在 `.env`；飞书/LLM 按需配置。  
**你会得到**：本机 HTTP API + 后台调度；浏览器可打开 Swagger。

```powershell
poetry run python -m app

# 等价（需自己带 host/port；不一定读 GOLDQUANT_PORT）
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8085
```

| 检查项 | 地址/命令 |
|---|---|
| Swagger 文档 | <http://127.0.0.1:8085/docs> |
| 健康检查 | <http://127.0.0.1:8085/health> |
| Linux 常驻 | `chmod +x run.sh && ./run.sh start`（需先 `poetry install`） |

联调可不碰外网：设 `GOLDQUANT_QUANT_USE_LOCAL_FIXTURE=true`，quant CLI 读 `data/fixtures/*.json`。

---

### 7.1 调度任务一览（默认时间）

配置键见 [CONFIG.md](./CONFIG.md)；实现：`app/scheduling/quant_scheduler.py`。

| 时间（默认） | 任务名 | 入口 | 必须性 | 用途（白话） |
|---|---|---|---|---|
| 05:00 | `prefetch_concepts` | `quant prefetch_concepts` | 可选 | 预取持仓/自选的概念与基本信息，减轻盘中耗时 |
| 8–22 整点 | `news` | `quant news` | 可选 | LLM 新闻摘要推送到飞书 |
| 09:25 | `pre_market` | `quant pre_market` | 建议 | 盘前指数/账户/关注推送 |
| 09:37–15:00 约每 7 分钟 | `during_market` | `quant during_market` | **模拟交易必须** | 盘中先按监控卖出，再按作战池择时买入（纸面） |
| 11:50 | `post_market_lunch` | `quant post_market_lunch` | 可选 | 午间复盘推送 |
| 收盘后（配置） | `post_market_evening` | `quant post_market_evening` | 可选 | 收盘复盘推送 |
| **18:00** | `update_daily` | `scripts.data.update_daily` | **数据必须** | 把当天行情写入正式库 |
| **20:10** | `daily_decision` | `quant daily_decision` | **选股必须** | 算因子选股，写作战池与卖出监控 |
| **周五 22:00** | `maintain` | `scripts.data.maintain` | **库自愈强烈建议** | 查缺补漏 + 当日增量 + 重试失败 |

不用调度、用系统「任务计划程序」时，可直接调同一解释器，例如：

```text
D:\workspace\GoldQuant\.venv\Scripts\python.exe -m scripts.data.update_daily --home D:\ProgramData\.quant
D:\workspace\GoldQuant\.venv\Scripts\python.exe -m quant daily_decision
D:\workspace\GoldQuant\.venv\Scripts\python.exe -m quant during_market
```

**选股怎么读报告、纸面怎么成交** → 专门文档 [DAILY_OPS.md](./DAILY_OPS.md)。

---

## 8. 因子研究脚本（可选）

**这一节在干什么**：用历史数据评估因子有效性、拟合权重、做 walk-forward，减少「拍脑袋权重」。日常选股不强制先做完这些（系统有默认/registry 权重）。  
**前置**：正式库就绪；部分步骤需要 `poetry install --extras ml`。  
**说明**：下列脚本均支持 `--home`。

---

### 8.1 构建因子面板 `build_panel`

**用途**：把指定区间每个交易日、每只股票的因子值算出来，存成一张面板（parquet），供 IC 报告、拟合权重等下游使用。  
**必须性**：做因子研究时必须先有面板（或等价数据）。  
**你会得到**：默认 `$QUANT_HOME/reports/panel/panel.parquet`。

```powershell
poetry run python -m scripts.factors.build_panel `
  --home D:\ProgramData\.quant --start 2024-01-01 --end 2026-08-07
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | 读库；影响默认 `--out` 根路径 |
| `--start` / `--end` | 必填 | 面板日期区间 |
| `--out` | `$QUANT_HOME/reports/panel/panel.parquet` | 输出路径 |

---

### 8.2 IC 报告 `ic_report`

**用途**：对已有因子面板计算 IC / ICIR 等，判断哪些因子历史上有预测力、方向是否正确。  
**必须性**：研究权重前建议跑。  
**前置**：已有 `--panel` 文件（常来自 §8.1）。  
**你会得到**：`$QUANT_HOME/reports/ic/` 下报告。

```powershell
poetry run python -m scripts.factors.ic_report `
  --home D:\ProgramData\.quant `
  --panel D:\ProgramData\.quant\reports\panel\panel.parquet
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | 影响默认报告目录 |
| `--panel` | 必填 | 因子面板 parquet 路径 |
| `--out` | `$QUANT_HOME/reports/ic` | 报告输出目录 |

---

### 8.3 拟合因子权重 `fit_weights`

**用途**：按 IC/统计显著性等规则筛选因子并拟合权重，写出可供决策使用的权重配置。  
**必须性**：可选；要用数据驱动静态/滚动权重时跑。  
**前置**：库覆盖拟合区间；建议先看过 IC 报告。  
**你会得到**：权重文件（写入配置约定路径，详见脚本输出日志）。

```powershell
poetry run python -m scripts.factors.fit_weights `
  --home D:\ProgramData\.quant --start 2022-01-01 --end 2026-08-07
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |
| `--start` / `--end` | 必填 | 拟合区间 |
| `--min-icir` | `0.0` | 入选最低 ICIR（0=不过滤） |
| `--min-tstat` | `1.0` | 入选最低 \|t\| |
| `--static` | off | 拟合静态权重（否则偏 walk-forward 式） |
| `--train-window` | `504` | 训练窗口长度（交易日） |
| `--step` | `63` | 滚动步长（交易日） |
| `--fdr-alpha` | `0.05` | BH-FDR 显著性；`0` 关闭 FDR |
| `--horizon` | `5` | IC 主持有期（交易日） |
| `--horizons` | `5,10,20` | 多持有期混合，逗号分隔 |

---

### 8.4 Walk-forward `walk_forward`

**用途**：按「训练窗口 → 测试窗口」滚动评估策略，并产出时序权重（`factor_weights_ts` 一类），减轻单段过拟合。  
**必须性**：可选；上线前做稳健性检查时强烈建议。  
**你会得到**：`$QUANT_HOME/reports/wf/` 报告与权重时序文件。

```powershell
poetry run python -m scripts.research.walk_forward `
  --home D:\ProgramData\.quant --start 2022-01-01 --end 2026-08-07
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |
| `--start` / `--end` | 必填 | 全样本区间 |
| `--out` | `$QUANT_HOME/reports/wf` | 报告目录 |
| `--train-months` | `24` | 每折训练月数 |
| `--test-months` | `6` | 每折测试月数 |
| `--max-positions` | `10` | 最大持股 |

---

### 8.5 退市偏差审计 `delist_bias_audit`

**用途**：检查选股结果是否严重偏向「活到今天的股票」、忽略退市股带来的偏差（幸存者偏差审计）。  
**必须性**：可选；写研究报告或怀疑偏差时跑。  
**你会得到**：终端/报告中的偏差统计。

```powershell
poetry run python -m scripts.research.delist_bias_audit `
  --home D:\ProgramData\.quant --as-of 2026-08-07
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根 |
| `--as-of` | 必填 | 评估日 |
| `--lookback-days` | `400` | 回看自然日窗口 |
| `--top-n` | `30` | 作战池规模（alpha top N） |
| `--refresh-delisted` | off | 重新拉取退市清单（默认用库内近似） |

---

### 8.6 滑点校准 `calibrate_slippage`

**用途**：根据纸面账户近期实际成交，估计滑点假设是否合理，供回测/撮合参数参考。  
**必须性**：可选；已有一段纸面成交记录后再跑更有意义。  
**你会得到**：滑点统计输出。

```powershell
poetry run python -m scripts.research.calibrate_slippage --home D:\ProgramData\.quant --days 90
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | 读取该 home 下纸面成交归档 |
| `--days` | `90` | 回看自然日 |

---

## 9. 报告落盘与本地自检

### 9.1 报告与账户目录

默认都在 `$QUANT_HOME` 下：

| 路径 | 用途 |
|---|---|
| `reports/decision/` | 日决策卡文本/JSON |
| `reports/ops/` | 推送正文归档 |
| `reports/bt/` | 回测报告 |
| `reports/wf/` | walk-forward |
| `reports/ic/` | IC 报告 |
| `reports/panel/` | 因子面板 |
| `reports/intraday_timing/` | 盘中择时验证 |
| `daily/{date}/raw\|derived\|trades\|review/` | 按日归档 |
| `paper_account/` | 纸面作战池、卖出监控、模拟持仓 |
| `state/` | 人工持仓（与纸面隔离） |

---

### 9.2 日决策脚本自检（可选）

**用途**：不依赖调度，手动跑一遍「今晚选股」流程，确认库与因子能出决策卡。  
**必须性**：联调/排查时可选。  
**说明**：`--dry-run` / `--no-push` 都不推飞书；加 `--no-paper` 则不写纸面文件。完整参数见 [DAILY_OPS.md](./DAILY_OPS.md)。

```powershell
poetry run python -m scripts.decision.daily --home D:\ProgramData\.quant --dry-run --no-push
```

---

### 9.3 端到端冒烟 `smoke_e2e`（可选）

**用途**：在**临时目录**里造最小合成库，跑面板 + 回测链路，验证代码安装是否正常。**不读写**你的真实 `D:\ProgramData\.quant`。  
**必须性**：改完代码或新环境安装后建议跑一次。  
**你会得到**：通过/失败日志；退出码非 0 表示链路坏了。

```powershell
poetry run python -m scripts.smoke_e2e
```

---

### 9.4 分层检查 `check_layering`（可选）

**用途**：静态检查 import 方向是否符合 `common ← quant ← {app, scripts}`，防止 `quant` 误依赖 `app`。  
**必须性**：改架构/挪模块后建议跑；与行情数据无关。  
**你会得到**：`[OK]` 或违规 import 列表。

```powershell
poetry run python -m scripts.check_layering
```

---

### 9.5 HTTP 健康检查（可选）

**用途**：确认 API 进程已起来（调度依赖它）。  
**必须性**：开了 `poetry run python -m app` 后可用来确认。

```powershell
curl http://127.0.0.1:8085/health
```

---

## 10. 复权约定（必读）

弄错复权会导致回测「假赚/假亏」：

| 场景 | 应该用什么 |
|---|---|
| 因子、回测收益、出场（ATR 等） | **`load_adjusted_daily()` 后复权价** |
| 纸面撮合、涨跌停判断 | **`daily_raw` 真实价** |
| 发现除权 | 系统内 `detect_ex_dividend_codes` → `refresh_adj_for_codes`（`update_daily` 会做） |

若 `validate_library` 报 adj 覆盖 FAIL 或复权跳空 FAIL：先保证 `build_daily` 跑完复权步骤，再查是否有脏价需 §3.6 清理。

---

## 11. 常见问题

**Q：`ModuleNotFoundError: No module named 'pandas'`？**  
A：你用的不是 Poetry 环境。请用 `poetry run python -m ...`，或先 `.\.venv\Scripts\Activate.ps1`。用 `poetry run python -c "import sys; print(sys.executable)"` 确认路径落在项目 `.venv`。

**Q：数据写到了错误目录 / 库是空的？**  
A：检查 `.env` 的 `GOLDQUANT_QUANT_HOME_DIR`、当前终端的 `QUANT_HOME`，以及命令是否传了正确的 `--home`。用资源管理器看该目录下是否出现 `store/daily_raw`。

**Q：`build_daily` 跑了很久中断了怎么办？**  
A：用**完全相同**的命令再跑一遍即可续传。不要随便改 `--start/--end` 除非你有意缩小窗口。

**Q：quant 报连接失败？**  
A：单次 `python -m quant ...` **不依赖** HTTP API。若仍报错，检查环境/fixture；只有要自动调度五时段时才必须 `poetry run python -m app`。

**Q：面板为空 / 日决策失败？**  
A：确认正式库 `validate_library` 通过，且当天（或最近交易日）已跑过 `update_daily`。

**Q：`.env` 端口不生效？**  
A：用 `poetry run python -m app` 启动；裸 `uvicorn` 需自己写 `--port`。

**Q：回测很好、纸面差距大？**  
A：正常现象。回测是日频差额调仓；纸面是 T+1 盘中 θ 触发。见 [ARCHITECTURE.md §4.5](./ARCHITECTURE.md#45-回测-vs-实盘纸面)。

**Q：价格 sanity FAIL？**  
A：按 §3.6：`scrub_invalid_bars --dry-run` → 确认 → `--apply` → 再 `validate_library`。

**Q：缺依赖 / ML 相关报错？**  
A：`poetry install`；研究脚本再加 `poetry install --extras ml`。不要再维护平行的 `requirements.txt`。

**Q：下一步如何每天选股、模拟买卖？**  
A：库养好并开调度后，按 [DAILY_OPS.md](./DAILY_OPS.md) 操作（18:00 增量 → 20:10 选股 → 次日盘中纸面成交）。
