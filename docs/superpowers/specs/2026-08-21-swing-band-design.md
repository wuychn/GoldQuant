# 波段状态机策略设计

日期：2026-08-21  
状态：v2 已改规则（回撤再起 / 关滞涨），回测中
范围：独立波段买卖规则（回测优先；确认后再接纸面）

## 1. 背景

用户目标（波段）：

- 波段上涨信号出现 → 买  
- 滞涨 / 转跌 / 时间成本 → 卖  

与现有 **SwapGate + 反转 IC 权重** 路径分离：本策略是显式趋势波段状态机，不复用「截面排名换仓」语义。

## 2. 已确认决策

| 项 | 选择 |
|----|------|
| 买入 | **3**：ATR 动量启动 **且** 位置不太弱 |
| 卖出 | **A**：转跌 / 滞涨 / 时间，**任一触发即清仓** |
| 仓位 | **C**：最多 3 只，按买入强度加权 |
| 持仓纪律 | 未触发卖出不因「外部更强」被换掉；只填空位 |
| 实现路径 | 独立波段策略模块，不塞进 SwapGate |

## 3. 规则定义

### 3.1 买入（须同时满足）

1. **ATR 动量**：近 `N=10` 个交易日涨幅 ≥ `a × ATR14`，默认 `a=1.5`  
2. **位置**：`dist_high_252` ≥ 当日可投宇宙截面分位 `P60`  
3. **过滤（默认开）**：收盘 ≥ MA20  

**强度**（用于排序与加权）：`strength = (近N日涨幅) / ATR14`（ATR≤0 则不可买）。

### 3.2 卖出（OR → 清仓）

| 名称 | 条件 | 默认 |
|------|------|------|
| 转跌 | 收盘 ≤ 持仓期最高收盘 − `m × ATR14` | `m=2.5` |
| 滞涨 | 持仓交易日数 ≥ `H` 且 `(现价/成本 − 1) < b × ATR14/成本` ※ | `H=8`, `b=0.5` |
| 时间 | 持仓交易日数 ≥ `K` 且 收益 < 0 | `K=20` |

※ 滞涨收益阈值用「`b × ATR / 入场价`」把 ATR 换成收益率，与转跌同尺度。

持仓期最高收盘：自买入日起滚动更新（与现有 ExitTracker 口径一致）。

### 3.3 组合

- `max_stocks = 3`  
- 每日顺序：① 对已持仓评估卖出并清仓 → ② 统计空位 → ③ 全市场（可投宇宙）扫买入信号 → ④ 按 strength 降序填空位  
- 新开仓：对当日新买入集合按 strength 加权，使新仓合计权重 = `full_invest × (新开只数/max_stocks)` 的简化，或：目标总仓 `full_invest`，已留仓保持原权重比例、剩余预算分给新开仓（实现取后者，避免无故降旧仓）  
- **第一版简化（推荐）**：已持仓未卖出则**权重保持不变**；仅新开仓之间按 strength 分配「剩余可投资预算」=`full_invest − sum(kept_weights)`  
- 单票上限 `max_weight`：默认 `0.45`

### 3.4 执行口径

- 回测：`strict_signals=True`（T-1 信号，T 开盘成交）  
- 成本：沿用现有 CostModel（含 ADV 滑点）  
- 宇宙：现有 `universe_codes` / 回测 daily 中可交易代码；停牌等由 broker 既有逻辑处理  

## 4. 模块落点

| 位置 | 职责 |
|------|------|
| `quant/swing/signals.py` | 买入信号、强度、卖出判定（纯函数，易单测） |
| `quant/swing/policy.py` | `SwingBandPolicy`：实现与 `TargetPortfolio` 相同的 `target_weights(alpha, prices, current, date)` 协议；**不依赖 alpha 排名**，内部用 `daily` 算信号（`alpha` 参数可忽略或仅作兼容） |
| `quant/config/quant.yml` → `swing_band:` | 参数段；`enabled` 默认 `false`，回测脚本显式开启，避免打断现有纸面 SwapGate |
| `scripts/research/swing_band_bt.py` | 同窗口回测（可与 IC alpha 解耦，本策略不需要 alpha pkl） |
| 单测 | 买/卖边界、空位填充、不踢未触发卖出的持仓 |

> 说明：协议上 `run_backtest` 仍要 `alpha_fn`；研究脚本可传空 dict 或占位。Policy 以 `self.daily` + `date` 为准。

## 5. 与现有系统关系

| 组件 | 关系 |
|------|------|
| SwapGate / IC 权重 / trend_force | **本版不改默认主路径**；波段策略并行存在 |
| `ExitConfig` 硬止损/ATR trailing | 波段回测时 `exit_config=None` 或关闭，卖出完全由策略规则完成，避免双出口 |
| 纸面 daily | 第一版只做回测验收；纸面接入列为后续 |

## 6. 默认参数汇总

```yaml
swing_band:
  enabled: false
  n_lookback: 10
  atr_period: 14
  momentum_atr_mult: 1.5
  dist_high_pctile: 60
  require_above_ma20: true
  ma_period: 20
  trail_atr_mult: 2.5
  stall_hold_days: 8
  stall_atr_mult: 0.5
  time_stop_days: 20
  max_stocks: 3
  full_invest: 0.95
  max_weight: 0.45
```

## 7. 验收

窗口建议：`2023-08-01`～`2025-12-31`（与前期对照同窗）。

指标：总收益、年化、Sharpe、MDD、换手、平均持仓天数、卖出原因归因（转跌/滞涨/时间）。

对照：

1. 本波段策略  
2. （参考）旧 `aw_top10_10d` 无出场结果（已知约 +53%，语义不同，仅作量级参考）

成功标准：规则行为符合设计（单测）；收益不作为第一版合并纸面的硬门槛，但若深度亏损需复盘参数而非先改纸面。

## 8. 非目标（第一版）

- 不接入盘中择时 / 作战池  
- 不与 SwapGate 同时作用于同一目标权重  
- 不做参数大网格（可后置小扫 `a/m/H/K`）  
- 不把反转 IC 权重混进买入条件  

## 9. 实现顺序

1. `signals.py` + 单测  
2. `SwingBandPolicy` + 单测  
3. yml 段 + 研究回测脚本  
4. 跑同窗口回测并汇报  
5. （可选）再议纸面开关  

## 10. 风险

- 买入偏动量，与历史反转边可能截然不同，勿用旧结论外推  
- `P60` 截面依赖当日宇宙完整性  
- 持仓权重冻结可能导致漂移；第一版可接受，后续再加再平衡开关  
