# GoldQuant r1 / r3 完成情况评估

> 评估日期：2026-07-26
> 评估对象：`r1`（修补分支，HEAD `2314c3d`）、`r3`（重构分支，HEAD `32e355f`）
> 评估基线：`b00a2e1`
> 评估方式：代码走查 + 实际运行验证（Python 3.11 / numpy 2.4.4 / pandas 3.0.2 / pyarrow 25.0.0）

---

## 一、核心判定

| 分支 | 代码完成度 | 可运行性 | 验收达成 | 结论 |
|---|---|---|---|---|
| r1 | 5/5 项已落地 | 可运行 | 无回归测试 | **可用，但有 1 处严重口径不自洽** |
| r3 | P0–P6 骨架 100% | **端到端不可运行** | DoD 0/31 | **骨架完整，但从未在真实数据上跑过** |

**一句话结论**：r3 的架构方向正确、模块划分清晰、28 个单元测试全绿，但这些测试全部基于合成数据，掩盖了三个阻塞性缺陷——入口脚本因 API 误用直接崩溃、日期格式冲突导致全面未来函数、出场状态机生命周期错误。**在修复这三项之前，r3 的任何回测结果都不可信。**

r1 作为过渡方案是可用的，但 `qfq` 与不复权价格混用会让回测估值系统性偏差，需优先修。

---

## 二、交付规模

```
r1:  11 文件, +1141 行   (含 docs/REBUILD_PLAN.md 531 行)
     实际代码约 610 行

r3:  65 文件, +4669 行
     生产代码约 3400 行, 测试约 750 行, 脚本约 500 行
```

r3 新增模块（均为全新命名空间，与旧系统并行）：

| 阶段 | 模块 | 文件数 |
|---|---|---|
| P0 数据地基 | `quant/data/` | 6 + 3 脚本 |
| P1 因子层 | `quant/factors/library/` + `registry/panel_builder/ic` | 10 + 2 脚本 |
| P2 回测框架 | `quant/backtest2/` | 7 + 1 脚本 |
| P3 组合层 | `quant/portfolio2/` | 4 |
| P4 出场层 | `quant/exit/` | 3 |
| P5 参数治理 | `quant/research2/` | 3 |
| P6 决策闭环 | `quant/decision/` + `quant/journal/` | 3 |

---

## 三、阻塞性缺陷（P0，必须先修）

### 缺陷 1：入口脚本 API 误用，从未执行过

`trading_days_between(start: date, end: date) -> int` 返回的是**交易日计数**，而 `trading_day_list()` 才返回日期列表。两个入口脚本都误用了前者，且传入 `str` 而非 `date`：

```python
# scripts/factors/build_panel.py:24  与  scripts/backtest/run.py:30
dates = trading_days_between(args.start, args.end)   # 返回 int，不可迭代
```

实际运行结果：

```
File "scripts/factors/build_panel.py", line 24, in main
    dates = trading_days_between(args.start, args.end)
File "quant/data/calendar.py", line 122, in trading_days_between
    cur = start + timedelta(days=1)
TypeError: can only concatenate str (not "datetime.timedelta") to str
```

**这是判定性证据**：脚本在第一行业务代码就崩溃，说明 P1 的面板构建与 P2 的端到端回测**从未被执行过一次**。旁证：`reports/` 目录不存在，`data/` 下只有旧系统的 json 快照，没有任何 parquet 离线库文件。

修复：

```python
from datetime import date
from quant.data.calendar import trading_day_list

dates = [d.strftime("%Y-%m-%d") for d in trading_day_list(
    date.fromisoformat(args.start), date.fromisoformat(args.end)
)]
```

### 缺陷 2：日期格式冲突导致全面未来函数

离线库写入 **ISO 格式**：

```python
# quant/data/fetch.py:62
out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
```

而引擎与脚本传入**紧凑格式** `%Y%m%d`。因子计算依赖字符串比较做时间切片：

```python
# quant/factors/panel_builder.py
sub_daily = daily[daily["date"] <= d]          # d = "20240115"
# quant/factors/library/base.py
self.df.loc[self.df.index <= as_of, "close"]
```

实测字符串比较结果：

```
"2024-01-10" <= "20240115"  ->  True
"2024-06-15" <= "20240115"  ->  True   ← 6月数据被当作1月之前
"2024-12-20" <= "20240115"  ->  True   ← 12月数据被当作1月之前
```

原因是 ASCII 中 `'-'`(0x2D) < 任何数字(0x30+)，所以**同年任何日期的 ISO 串都小于任何紧凑串**。后果：一旦在真实离线库上运行，每个评估日的因子都会用到全年数据，IC 会虚高到毫无意义。

`quant/data/test_data_layer.py` 的 `test_pit_no_future_leak` 没能捕获它，因为合成数据自己内部格式一致。

修复：全链路统一 ISO；比较前转 `pd.to_datetime` 或直接用 `date` 对象；加一条跨格式断言的回归测试。

### 缺陷 3：ExitTracker 生命周期错误

再平衡卖出（含清仓）路径**没有** `tracker.close()`：

```python
# quant/backtest2/engine.py:128-139
for code in list(broker.holdings.keys()):
    ...
    broker.sell(code, rows_by_code[code], prev_closes.get(code), target_shares=sell_shares)
    # 缺 tracker.close(code)
```

而买入时用 `code not in tracker.all()` 作为 open 的条件：

```python
# quant/backtest2/engine.py:153-154
if tracker is not None and code not in tracker.all():
    tracker.open(code, broker.holdings[code].cost_price, broker.holdings[code].buy_date)
```

组合起来的后果：清仓后 `ExitState` 残留 → 重新买回时条件为 False → **跳过 open** → 沿用上一轮持仓的 `entry_price` 和 `highest_close`。ATR 跟踪止损基准完全错误，可能在建仓当天就触发止损。加仓时 `cost_price` 变化后 tracker 也不同步。

修复：`broker.sell` 后若 `shares == 0` 则 `tracker.close(code)`；买入后统一 `tracker.open`（或新增 `tracker.upsert`）。

---

## 四、r1 逐项验收

| # | 修补项 | 判定 | 说明 |
|---|---|---|---|
| 1 | 回测零成交 | **对症修复** | `_inject_state` 改为以 rich 行为底、仅叠加 `WATCHLIST_META_KEYS` 元数据，字段优先级正确，覆盖了 `_evaluate_buy_candidate` 所需的 `历史行情`/`盘口`/`技术指标`/`上市时间` 等主路径字段 |
| 2 | 交易日历 | **核心正确** | `(start, end]` 语义与「买入日之后计数」一致；`d > max(cal)` 走工作日兜底合理 |
| 3 | 复权口径 | **不自洽** | 见下方严重问题 |
| 4 | 基准对比 | **部分正确** | 日期对齐与 beta/跟踪误差公式标准，但超额收益口径有误 |
| 5 | 停牌建模 | **半完成** | `gates/rules.py` 只接了启发式版本，网络版 `suspended_codes` 实现了但无调用点 |

### r1 严重问题：复权口径混用

- 实盘 enrich 走 `dfcf_util.hist` 新默认 `qfq`（`app/utils/dfcf_util.py:151`）
- 回测估值 `_make_close_provider` 显式用不复权（`quant/backtest/engine.py:239`）
- 而买入价来自快照的 `quote_last_price`，是 **qfq 口径**

结果：买入价（qfq）与估值价（不复权）不同基准，跨除权日的持仓盈亏会系统性偏差。同时 `dfcf_util.py:156-157` 的注释写「回测离线库应改用 hfq」，与实现不一致。

修复：二选一并全链路统一——要么 `close_provider` 也用 qfq，要么回测快照 enrich 改不复权。

### r1 中等问题

| 位置 | 问题 |
|---|---|
| `quant/research/metrics/benchmark.py:131-132` | `excess_return_vs_benchmark = Σ(s_i - b_i)` 是日超额算术累加，既非总收益差也非年化；与 `performance.py:97-99` 的 fallback 口径不一致 |
| `quant/data/calendar.py:71` | `if days and _calendar_path().parent.exists() or True:` 恒为 True，抓取失败时会把**空数组**写入缓存 json，污染后续读取 |
| `quant/data/calendar.py:26-40` | `_load_calendar` 用 `lru_cache(maxsize=1)`，进程内只读一次，跨日长跑或 `refresh_calendar()` 后不刷新 |
| `quant/data/calendar.py:89-96` | 假日 API 失败时回退 `weekday() < 5`，会把春节等长假误判为交易日 |
| `quant/scoring/tech_indicators.py:74-80` | `quote_last_price` 会回退 `技术指标.last_close`，导致盘口为空但有归档指标时 `is_suspended_from_row` 返回 False，漏拦真停牌 |
| 全分支 | 5 项修补**无一条 dedicated 回归测试**，尤其没有「回测能产生成交」的断言 |

---

## 五、r3 逐项验收

### P0 数据地基

**已实现**：parquet 分年分区读写、PIT universe（ST/新股/停牌/低流动性过滤）、交易日历、后复权因子、build/update/verify 三脚本。10 个 unittest 通过。

**问题**：

| 严重度 | 位置 | 问题 |
|---|---|---|
| 严重 | `quant/data/fetch.py:62` | ISO 日期格式与下游冲突（见缺陷 2） |
| 中等 | `quant/data/universe.py:75-79` | `as_of` 为非交易日时 `latest = daily["date"].max()` 可能跳到数据集最新日，静默泄露；应改用 `prev_trading_day(as_of)` |

**DoD**：5 条中仅「universe 不引用 T 之后数据」有单测覆盖，其余 4 条（人工抽查 3 个日期、20 只跨除权股连续性、update 连跑 3 日、verify 检出漏抓）**均未验证**，因为离线库从未建成。

### P1 因子层

**已实现**：6 族因子（动量/质量/位置/量能/资金/主题）共 14 个，方向统一为「越大越看多」，注册表、面板构建、行业+市值中性化、IC/ICIR/t值/五分位多空。

**问题**：

| 严重度 | 位置 | 问题 |
|---|---|---|
| 严重 | `quant/factors/panel_builder.py:26-40,100-101` | `_industry_map()` 取**当前**行业映射写入历史行，中性化回归用了非 PIT 的行业哑变量 |
| 严重 | `quant/factors/panel_builder.py:154-167` | `_forward_returns_batch` 外层遍历 codes、内层遍历全部 rows（`if r.code != code: continue`），复杂度 O(\|codes\|×\|rows\|)。5000 票 × 225 万行 ≈ 112 亿次比较 |
| 严重 | `quant/factors/panel_builder.py:105-133` | `build_panel` 对每个 (date, code) 重复做 `daily[daily["code"]==code]` + `sort_values`。750 日 × 3000 票 × 14 因子 ≈ 3400 万次因子调用，且每日重扫 |
| 中等 | `quant/factors/ic.py:74-98` | `ic_decay` 依赖 `row.meta['fwd']`，但 `panel_builder` 只写 `forward_return_pct`，多档衰减**恒为 0** |
| 中等 | `quant/factors/ic.py:112` | `quintile_spread` 排序用 `-np.inf` 兜底缺失，缺失样本全进最低分位污染空头组；而 `daily_rank_ic` 是 skip，两处口径不一致 |
| 轻微 | `library/flow.py:17`, `theme.py:17` | 两个因子恒返回 None（占位），仍注册进 `ALL_FACTORS` 且 `default_weight=0.6` |

**DoD**：5 条全部未验证。「10 分钟内重建面板」「5 年 IC 报告」「≥6 因子 ICIR>0.3 且五分组单调」「相关矩阵去共线」都需要真实数据；「每个因子符合手算预期」的现有测试只断言「非 None 且有限」，不构成手算校验。

### P2 回测框架

**已实现**：A 股成本模型（佣金/印花/过户/滑点）、可交易性判定、SimBroker（现金+持仓+T+1锁+成交明细+权益曲线）、日频引擎、绩效指标、报告导出。

**问题**：

| 严重度 | 位置 | 问题 |
|---|---|---|
| 严重 | `quant/backtest2/engine.py:76,105-152` | T 日收盘决策 + T 日收盘成交。`alpha_fn(T, ...)` 用到含 T 日收盘的信息，实盘中收盘前无法得知收盘价，构成乐观偏差 |
| 严重 | `quant/backtest2/tradability.py:43-50` | 涨跌停仅识别一字板（O==H==L==C）。真实涨停多为盘中封板，会被判为可买可卖 |
| 严重 | `quant/backtest2/tradability.py:47-49` | 硬编码 9.7%/9.3% 阈值，**无 ST 5%、创业板/科创板 20%、北交 30% 分支** |
| 严重 | `quant/backtest2/engine.py:128-139,153-154` | ExitTracker 生命周期错误（见缺陷 3） |
| 严重 | `quant/backtest2/engine.py:73-77` | 每日对每只票调 `_prev_close_map`，每次全表布尔过滤。750×5000 量级下扫描约 28 亿行·次 |
| 中等 | `quant/backtest2/engine.py:122-124` | `max_positions` 在 buffer **之后**二次截断，而 `TargetPortfolio` 内部已 `truncate_to_n`。若两者不等会砍掉 buffer 有意保留的仓位 |
| 轻微 | `quant/backtest2/engine.py:127` | `target_codes` 定义后未使用（死变量） |
| 轻微 | `quant/backtest2/broker.py:126-131` | `force_liquidate_suspended` 方法体只有 `continue`，等于空操作，且全仓无调用点 |

**DoD**：5 条全部未验证。其中两条最关键的**统计检验从未执行**：
- 随机基准检验（alpha 换随机数，结果应接近等权全A扣成本）
- 前视泄漏检验（因子前移 1 日，Sharpe 应显著上升）

这两条恰恰是能捕获缺陷 2 的手段。

### P3 组合层

**已实现**：逆波动率加权、目标波动缩放、缓冲区、单票/行业上限、持仓数截断。5 个单元测试通过。

**问题**：

| 严重度 | 位置 | 问题 |
|---|---|---|
| 中等 | `quant/portfolio2/voltarget.py:64-65` | `scale_to_target_vol` 只等比缩放不归一化，低波目标时权重和可 > 1（实测 3.72）。`TargetPortfolio` 第 5 步会压回 ≤0.95，但函数单独调用有杠杆语义 |
| 轻微 | `quant/portfolio2/voltarget.py:32-43` | `inv_vol_weights` 封顶迭代后未最终归一化，权重和可能 ≠ 1 |

组合方差公式 `var + corr*(pair - diag)` 经核验**正确**（等价于常相关矩阵形式）。缓冲区返回当前权重的语义与引擎差分逻辑**自洽**。

**DoD**：4 条中 2 条（约束生效性单测、权重和与单票上限）已由单测覆盖；2 条（换手率下降 ≥40% 且 Sharpe 不劣化、波动率目标降低回撤提升 Calmar）需真实回测，未验证。

### P4 出场层

**已实现**：Wilder ATR、四类止损（硬止损/ATR跟踪/MA20趋势/时间）、优先级排序、持仓极值追踪、引擎集成。7 个单元测试通过。

**问题**：

| 严重度 | 位置 | 问题 |
|---|---|---|
| 严重 | `quant/backtest2/engine.py` | ExitTracker 生命周期（见缺陷 3），直接导致 ATR 跟踪止损基准错误 |
| 中等 | `quant/exit/rules.py:59-69` | `time_stop` 无 `calendar_fn` 时用**自然日差**，`max_hold_days=20` 实际约 14 个交易日，误差 ~30%，长假更大 |
| 中等 | `quant/backtest2/engine.py:64` | `ExitConfig.calendar_fn` 默认 None，回测默认走自然日回退 |

**DoD**：4 条中「单测验证每条规则触发点」部分达成但有放水（见第六节）；「按出场类型盈亏归因」「最高价追踪回测与实盘口径一致」「尾部风险改善」均未验证。

### P5 参数治理

**已实现**：walk-forward 滚动 train/test、Deflated Sharpe、参数敏感性扫描与稳定性评分。5 个单元测试通过。

**问题**：

| 严重度 | 位置 | 问题 |
|---|---|---|
| 严重 | `quant/research2/significance.py:24-32` | `_expected_max_sharpe` 两个分支不一致：`n_trials>50` 用 `H_{n-1}-γ`，`≤50` 用 `H_{n-1}` 不减 γ，在 n=50/51 处跳变。且两式均非 Bailey 标准的 `Φ⁻¹(1-1/N)` 形式。同仓库旧模块 `quant/research/significance.py:119-129` 反而更接近论文 |
| 中等 | `quant/research2/significance.py:51-52` | `gamma` 计算后未使用（死变量）；`var_sh` 的偏度项与旧实现不一致且无文献依据 |
| 轻微 | `quant/research2/sensitivity.py:54` | `return int(...) if False else n_values_per_param ** n_params`，左侧恒假的死分支，`n_params` 参数实际无意义 |

**DoD**：5 条全部未验证（可调参数 ≤25、OOS/IS Sharpe >0.5、DSR >0、±30% 扰动稳健、分年度亏损年 ≤1）。这些是重构最核心的价值主张，目前全部空缺。

### P6 决策闭环

**已实现**：决策卡（目标/动作/出场/Alpha Top）+ 文本渲染、偏离日志（服从率/情绪化偏离）、漏斗转化率。4 个单元测试通过。

**问题**：

| 严重度 | 范围 | 问题 |
|---|---|---|
| 严重 | 全模块 | `quant/decision/` 与 `quant/journal/` **无任何生产调用点**，只被自己的测试引用。未接入 `app/` 或 `quant/orchestrator.py` |

**DoD**：3 条全部未达成（每日目标持仓自动推送、偏离记录录入+月度归因、漏斗通过率可查+告警）。模块存在但未接线，等于功能不可用。

---

## 六、测试质量评估

28 个新增测试全部通过，但**无法捕获上述任何一个 P0 缺陷**。原因：

1. **全部使用合成数据**，且合成数据自身日期格式一致，绕过了格式冲突问题。
2. **无端到端脚本测试**，所以 `trading_days_between` 误用直到今天才被发现。
3. **存在条件断言放水**：

```python
# quant/exit/test_exit.py:51-56
sig = trend_stop(df, ma_period=20)
if sig is not None:                      # 下跌序列不一定破 MA20
    assert sig.reason == "trend_stop_ma20"   # 测试可能空跑通过
```

```python
# quant/exit/test_exit.py:66-71
assert sig.reason in ("hard_stop", "atr_trailing", "trend_stop_ma20", "time_stop")
# 四种皆可，等于没验证优先级
```

4. **关键路径未覆盖**：`build_panel`、`_forward_returns_batch`、`TargetPortfolio` 在引擎中的表现、涨跌停/T+1 失败路径、出场集成、日期格式一致性、性能基准。

---

## 七、DoD 达成率汇总

| 阶段 | DoD 总数 | 完全达成 | 部分达成 | 未验证 |
|---|---|---|---|---|
| P0 | 5 | 1 | 0 | 4 |
| P1 | 5 | 0 | 1 | 4 |
| P2 | 5 | 0 | 0 | 5 |
| P3 | 4 | 2 | 0 | 2 |
| P4 | 4 | 0 | 1 | 3 |
| P5 | 5 | 0 | 0 | 5 |
| P6 | 3 | 0 | 0 | 3 |
| **合计** | **31** | **3** | **2** | **26** |

26 条未验证项中，绝大多数需要真实离线库 + 可运行的端到端回测。这正是缺陷 1、2 阻塞的部分。

---

## 八、修复路线图

### 第一梯队（阻塞端到端，约 1 天）

1. **统一日期格式**为 ISO，全链路 `pd.to_datetime` 比较或改用 `date` 对象；补跨格式回归测试。
2. **修入口脚本**：`trading_days_between` → `trading_day_list`，参数转 `date`。
3. **修 ExitTracker 生命周期**：清仓时 `close`、买入时统一 `open`/`upsert`。

完成后即可建库跑通第一次端到端回测（pyarrow 25.0.0 已就绪）。

### 第二梯队（结果可信度，约 2 天）

4. **性能三处优化**：`build_panel` 按 code 预分组、`_forward_returns_batch` 向量化 `shift(5)`、`_prev_close_map` 预计算查找表。目标：全 A 3 年在 5 分钟内。
5. **撮合真实性**：改 T-1 信号 / T 开盘成交；`tradability` 增加 ST 5% / 创业板科创板 20% / 北交 30% 分档，涨跌停判定改为封板逻辑而非一字板。
6. **行业映射 PIT 化**：落库历史行业快照，或退化为仅市值中性并在文档标注。

### 第三梯队（统计严谨性，约 1 天）

7. **跑两项泄漏检验**：随机 alpha 基准检验、因子前移 1 日检验。这是验证前六项修复是否到位的关键手段。
8. **修 DSR 公式**：对齐 Bailey & López de Prado (2014)，或直接复用 `quant/research/significance.py`；删除 `gamma` 死变量。
9. **修 IC 口径**：`quintile_spread` 缺失值改为排除；`ic_decay` 要么在 `panel_builder` 写入 `meta['fwd']`，要么删除。

### 第四梯队（接线与收尾）

10. **P6 接入主链路**：`quant/decision/` 与 `quant/journal/` 挂到 `orchestrator` 与推送链路，否则等于死代码。
11. **清理死代码**：`sensitivity.py:54`、`engine.py:127`、`broker.py:126-131`、`significance.py:51`。
12. **补测试**：端到端脚本 smoke、日期格式断言、出场集成、`TargetPortfolio` 在引擎中的行为；修掉两处条件断言放水。

### r1 独立修复项

13. **统一复权口径**（严重）：买入价与估值价同基准。
14. `benchmark.py` 超额收益口径统一为总收益差或年化。
15. `calendar.py:71` 死代码 `or True` 移除，避免空日历污染缓存。
16. 补「回测能产生成交」的回归测试，锁住零成交修复。

---

## 九、对分支取舍的建议

**不建议现在合并 r3 到主干。** 理由：核心价值主张（PIT 无泄露、可信回测、参数治理）目前一条都没有被真实数据验证过，而已发现的日期格式缺陷会让所有回测结果虚高。合并进去会给人「已完成」的错觉。

**建议路径**：

1. 短期继续用 r1 作为生产分支，但先修复权口径（第 13 项）。
2. 在 r3 上按第一梯队修完 3 项，建库跑出第一份端到端回测报告 + 两项泄漏检验。
3. 只有当随机基准检验和前视泄漏检验都通过后，r3 的因子 IC 与回测指标才有讨论价值；届时再决定是否合并。
4. r3 与旧系统目前是完全并行的命名空间（`backtest2`/`portfolio2`/`research2`），并行期无冲突风险，可以从容验证。

---

## 十、客观评价

架构设计是这次重构做得最好的部分：截面选股与时序出场分离、目标组合替代逐信号交易、参数治理独立成层、决策闭环留了人机协同接口。模块边界清晰，命名空间隔离让新旧系统可以并行演进，这些都是对的。

但工程验证严重不足。28 个绿灯测试给出了虚假的完成信号——它们验证的是「函数在理想输入下不崩溃」，而不是「系统在真实数据上产出可信结果」。三个 P0 缺陷中有两个（脚本崩溃、日期泄露）只要跑过一次真实的端到端流程就会立刻暴露，而这一次从未发生。

**当前状态的准确描述是：一套设计良好、单元自洽、但尚未经过任何真实数据检验的骨架。** 距离「可用于辅助决策」还差第一至第三梯队约 4 天的工作量，其中最关键的产出不是代码，而是那两份泄漏检验报告。
