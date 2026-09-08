# 每日选股与模拟交易

> 前置：离线库已按 [OPERATIONS.md](./OPERATIONS.md) 建好并通过 `validate_library`；正式库路径已写入 `.env`（如 `GOLDQUANT_QUANT_HOME_DIR=D:\ProgramData\.quant`）。  
> 本文只讲**数据完备之后**每天怎么选股、怎么做纸面模拟交易。

```mermaid
flowchart LR
  updateDaily["18:00 update_daily"] --> dailyDecision["20:10 daily_decision"]
  dailyDecision --> battlePool["作战池 + sell_watch"]
  battlePool --> duringMkt["T+1 during_market"]
  duringMkt --> paperAcct["paper_account 撮合"]
```

---

## 1. 每日闭环（必须理解）

| 时机 | 做什么 | 入口 | 必须性 |
|---|---|---|---|
| T 日 18:00 | 拉取当日行情与快照 | `scripts.data.update_daily` | **必须**（否则决策缺当日数据） |
| T 日 20:10 | 选股、写作战池与卖出监控 | `quant daily_decision` / `scripts.decision.daily` | **必须**（选股） |
| T+1 盘中每 ~7 分钟 | 先按监控卖出，再按作战池择时买入 | `quant during_market` | **必须**（模拟成交） |
| 周五 22:00 | 库自愈 | `scripts.data.maintain` | 强烈建议 |

T 晚**只定计划、不撮合买入**；真正买卖在 T+1 盘中完成。

当前默认策略是 **动量双槽**（`quant.yml` → `momentum_swing.enabled`）：

- T 晚：昨日收盘涨幅 Top6（流动池）写入次日作战池；持有到期的仓位写入 `sell_watch`（尾盘卖）
- 门控：沪深300 收盘 > MA55 才开新槽；连续空仓满 10 个交易日则半槽强制开一次
- T+1：开盘附近买入（跳过开盘涨停，不等盘中 θ）；到期日 14:30 后卖出

`momentum_swing.enabled: false` 时回退到 IC 因子 + SwapGate（目标组合差额 + 盘中 θ 择时）。

组合层 SwapGate 仅在动量关闭时生效。详见 [CONFIG.md](./CONFIG.md) `momentum_swing` / `swap_gate` / `exit`。

---

## 2. 选股：晚间日决策

### 2.1 调度（推荐）

启动 API 且 `scheduler.enabled: true` 后，默认 **20:10** 自动跑（见 `quant.yml → scheduler.daily_decision_time`）。

```powershell
poetry run python -m app
```

### 2.2 手动执行

```powershell
# 与调度等价（走 quant 入口）
poetry run python -m quant daily_decision

# 直接跑脚本（可指定 home / 干跑）
poetry run python -m scripts.decision.daily --home D:\ProgramData\.quant
poetry run python -m scripts.decision.daily --home D:\ProgramData\.quant --no-push
poetry run python -m scripts.decision.daily --home D:\ProgramData\.quant --dry-run
poetry run python -m scripts.decision.daily --home D:\ProgramData\.quant --no-paper
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `--home` | 当前 QUANT_HOME | quant-home 根；纸面账户在该 home 下的 `paper_account/` |
| `--date` | 今天（非交易日回退） | 决策日 |
| `--out` | `$QUANT_HOME/reports/decision` | 决策报告目录 |
| `--n-enter` | `8` | 目标组合纳入阈值排名 |
| `--n-exit` | `15` | 目标组合剔除阈值排名 |
| `--max-positions` | `10` | 最大持仓只数 |
| `--battle-pool-size` | `30` | 作战池规模（alpha top N，供次日盘中择时） |
| `--no-paper` | off | 只出决策卡，不写作战池 / sell_watch / 纸面快照 |
| `--dry-run` | off | 完整跑决策但不推飞书 |
| `--no-push` | off | 不推飞书（仍写报告与纸面文件） |

### 2.3 产出什么

| 产出 | 路径 | 说明 |
|---|---|---|
| 决策卡文本/JSON | `$QUANT_HOME/reports/decision/decision_{date}.*` | 目标权重、动作、alpha top、漏斗 |
| 作战池 | `$QUANT_HOME/paper_account/battle_pool/{T+1}.json` | 次日可买候选 |
| 卖出监控 | `$QUANT_HOME/paper_account/sell_watch/{T+1}.json` | 持仓止损/出场阈值 |
| 纸面快照 | `reports/decision/paper_{date}.json` | 账户与持仓只读快照 |
| 飞书「晚间复盘」 | 推送 | 除非 `--dry-run` / `--no-push` |

权重来源：`walk_forward` / 静态校准 / registry 默认（未校准时决策卡会标注）。

---

## 3. 模拟交易：盘中纸面撮合

### 3.1 账户位置

纸面与人工仓隔离：

```text
$QUANT_HOME/paper_account/
├── battle_pool/     # T 晚写入，T+1 买入用
├── sell_watch/      # T 晚写入，T+1 卖出监控
└── state/           # 纸面持仓与权益
```

人工持仓仍在 `$QUANT_HOME/state/`（account.json / holding.jsonl），互不覆盖。

### 3.2 盘中 `during_market`

**调度**：默认 09:37–14:59 每约 7 分钟（`scheduler.during_market_times`）。

```powershell
poetry run python -m quant during_market
```

逻辑概要（先卖后买）：

1. 读当日 `sell_watch` → 动量到期（`when=close`）在 14:30 后卖；其它止损仍盘中触发  
2. 读当日 `battle_pool` → **动量**：按排名开盘买（跳过开盘涨停）；**IC 回退**：盘中因子超过 θ 才买  
3. 风控闸门（日初权益、仓位上限等）约束下单  
4. 推送「智能盯盘」（指数 / 持仓 / 异动 + 成交摘要）

**与回测差异**：回测是日频 strict 差额 rebalance；纸面是 T+1 盘中择时触发。回测 Sharpe 不能直接当实盘预期。详见 [ARCHITECTURE.md §4.5](./ARCHITECTURE.md#45-回测-vs-实盘纸面)。

### 3.3 其它时段推送（建议开，非撮合核心）

| 时间 | 模式 | 飞书标签 | 作用 |
|---|---|---|---|
| 09:25 | `pre_market` | 开盘啦 | 指数 / 纸面账户 / 关注 |
| 11:50 | `post_market_lunch` | 午间复盘 | 午前指数 + 账户 |
| 收盘后 | `post_market_evening` | 收盘复盘 | 收盘指数 + 纸面绩效 |
| 整点 | `news` | 新闻聚焦 | LLM 新闻摘要 |
| 05:00 | `prefetch_concepts` | — | 预取概念，加速盘中 |

```powershell
poetry run python -m quant pre_market
poetry run python -m quant post_market_lunch
poetry run python -m quant post_market_evening
poetry run python -m quant news
```

---

## 4. 一键定时：启哪些、怎么启

### 4.1 启动方式（生产必须）

1. `.env` 指向正式库（`GOLDQUANT_QUANT_HOME_DIR`）  
2. `quant/config/quant.yml`（或 `$QUANT_HOME/config/quant.yml` 覆盖）中 `scheduler.enabled: true`  
3. 项目根执行：

```powershell
poetry run python -m app
```

调度由 `app/scheduling/quant_scheduler.py` 注册；漏触容忍见 `misfire_grace_sec`。

### 4.2 任务全表（默认）

| 时间 | 任务 | 对选股/模拟的意义 |
|---|---|---|
| 05:00 | prefetch_concepts | 加速盘中概念相关逻辑 |
| 8–22 整点 | news | 信息推送，不参与撮合 |
| 09:25 | pre_market | 盘前状态 |
| 09:37…14:59 | **during_market** | **纸面买卖** |
| 11:50 | post_market_lunch | 午间状态 |
| **18:00** | **update_daily** | **当日数据入库** |
| **20:10** | **daily_decision** | **选股 + 写池** |
| 周五 22:00 | maintain | 库缺口自愈 |

键名与改时间方式见 [CONFIG.md](./CONFIG.md) 的 `scheduler.*`。

### 4.3 不用调度时的最低手动集

每个交易日：

```powershell
# 盘后
poetry run python -m scripts.data.update_daily --home D:\ProgramData\.quant
poetry run python -m scripts.decision.daily --home D:\ProgramData\.quant

# 次日盘中（需自行定时或多次手动）
poetry run python -m quant during_market
```

每周五晚：

```powershell
poetry run python -m scripts.data.maintain --home D:\ProgramData\.quant
```

---

## 5. 常见操作

**只想看今天选了谁、不推飞书**

```powershell
poetry run python -m scripts.decision.daily --home D:\ProgramData\.quant --no-push
```

**完全不碰纸面账户**

```powershell
poetry run python -m scripts.decision.daily --home D:\ProgramData\.quant --no-paper --no-push
```

**怀疑库缺日 / 决策异常**

```powershell
poetry run python -m scripts.data.validate_library --home D:\ProgramData\.quant
poetry run python -m scripts.data.maintain --home D:\ProgramData\.quant
```

**改策略参数（持仓数、作战池）**  
优先改 `$QUANT_HOME/config/` 覆盖或 CLI 参数；长期权重用研究脚本（OPERATIONS §8）校准后再跑决策。

---

## 6. 相关文档

| 文档 | 用途 |
|---|---|
| [OPERATIONS.md](./OPERATIONS.md) | 从零建库、update/maintain、回测、参数表 |
| [CONFIG.md](./CONFIG.md) | `.env` / `quant.yml` / scheduler 键 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | 选股→买卖闭环与回测/实盘差异 |
| [FACTORS.md](./FACTORS.md) | 因子含义与权重 |
