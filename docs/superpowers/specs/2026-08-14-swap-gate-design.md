# SwapGate 换仓门槛设计

日期：2026-08-14  
状态：已实现（核心代码落地；对照回测待跑）  
范围：组合换仓决策（回测 + 纸面共用）

## 1. 背景与目标

固定「每 N 日换仓」或「每日追 TopN」都不符合波段语义。正确口径是：

- 每天评估，但**日期不是第一标准**
- **不**因为排名第一就自动买入
- 手上票若仍符合策略 / 趋势未坏，或与新票分数差异不够大，**可以不调仓**
- 换仓必须同时通过 **分数差** 与 **成本覆盖**（含 ADV 冲击）

成功标准：

- 换手显著低于「日频追 TopN」
- 同窗口绩效不低于可接受波段水平，并优于「日频 + 紧止损」路径
- 回测与纸面共用同一套 gate / 趋势 / 出场参数，无双逻辑打架

## 2. 决策摘要（已确认）

| 项 | 选择 |
|----|------|
| 架构 | 独立 `SwapGate`（方案 2） |
| 换仓门槛 | C：分数差 δ **且** 成本覆盖 k×往返成本 |
| 仍符合策略 | B：alpha 可持有区 + 趋势系统 |
| 趋势软判定 | ATR 回撤带 + MA20（可扩展结构低点） |
| 趋势硬出场 | B：趋势条件连续 N 日不满足 → 强制卖（不走换仓门槛） |
| N | **2** |
| 持仓规模 | `max_stocks=3`, `n_enter=3`, `n_exit=8` |
| 权重 | alpha 加权（非日频 MVO） |
| 成本 | 按 ADV + 参与率估冲击（复用现有 slippage 口径） |
| 出场治理 | 本版与 SwapGate 一并设计并对齐纸面 `sell_watch` |

## 3. 核心规则

### 3.1 每日流程

1. 计算截面 alpha  
2. 对每只已持仓打标：`keep` / `replaceable` /（内部）`force_exit`  
3. `SwapGate` 决定目标持仓集合（可大量保持不变）  
4. 对目标集合做 alpha 加权，得到目标权重  
5. 决策卡 / 回测按目标权重交易；`force_exit` 优先卖出

### 3.2 持仓标签

**趋势 OK（第一版主判定式）：**

- 收盘 ≥ 持仓期最高收盘 − `m × ATR`  
- **且** 收盘 ≥ MA20  

可选增强（实现时可开关）：未破持仓期近 N 日结构低点。

**alpha 可持有区：** 排名 ≤ `n_exit`（默认 8）。

| 标签 | 条件 |
|------|------|
| `keep` | alpha 可持有 **且** 趋势 OK |
| `replaceable` | 非 `keep`，且未触发强制卖 |
| `force_exit` | 趋势条件连续 `trend_fail_days`（默认 2）日不满足 |

说明：

- `keep`：**不因**「外部有更强第一名」被换出  
- `replaceable`：可被替换，但必须过双门槛  
- `force_exit`：**必须卖出**，不走分数差/成本门槛  

### 3.3 换仓双门槛（同时满足）

对「候选新票 new」替换「最弱 replaceable old」：

1. **分数差：** `α_new − α_old ≥ δ`，其中 `δ = δ_σ × σ(α)`（当日截面 alpha 标准差）  
2. **成本覆盖：** 预估边 ≥ `k × round_trip_cost(old, new)`  

`round_trip_cost` 使用 ADV 感知冲击（见 §5），不是固定名义费率。

不满足 → 不换，继续持有该 `replaceable`（除非之后变成 `force_exit`）。

### 3.4 空位与开仓

- 未满 `max_stocks`：仅当候选排名 ≤ `n_enter` 时可开仓  
- 纯空仓：排名 ≤ `n_enter` 即可建仓（无 old 可比时，成本门槛按「建仓单边成本 × k」检查，或第一版对空仓只要求 `n_enter`）  
- 满仓：只能替换 `replaceable`，且过双门槛；**禁止**为追第一名踢掉 `keep`

### 3.5 权重

- `equal_weight=false`, `alpha_weighted=true`  
- `alpha_shrink` 默认约 0.3（可配）  
- **第一版主路径关闭日频 MVO**（`optimizer=rank_vol`）

## 4. 趋势系统与强制出场

### 4.1 软 vs 硬

| 机制 | 作用 |
|------|------|
| 趋势软判定 | 决定 `keep` / `replaceable` |
| 连续 2 日趋势失败 | `force_exit`，次日（或信号日）强制卖 |
| SwapGate | 日常换股（值不值得换） |

### 4.2 与旧 exit 的关系

- 旧 hard_stop / 紧 ATR 强制卖 **不再作为日常主引擎**  
- 纸面 `sell_watch` 与本设计对齐：  
  - 强制卖原因以 `trend_fail_streak` 为主  
  - 可选保留「极端兜底」开关（默认关），避免再吞边  
- 回测 `ExitConfig` 与 live 共用同一参数源，禁止三处魔法数漂移

### 4.3 状态追踪

需为每只持仓维护（回测 ExitTracker / 纸面持仓 meta）：

- `highest_close`  
- `trend_fail_streak`（连续失败日数）  
- `buy_date` / entry 复权口径（与现有一致）

## 5. ADV 成本模型（换仓门槛用）

目标：小票更难换、大票更容易过线。

对候选交易估算：

- 使用标的 ADV（金额）与目标成交名义本金 → 参与率  
- 冲击沿用现有 `sqrt_law` / `SlippageContext` 口径  
- 往返 ≈ 卖旧冲击 + 买新冲击 + 佣金 + 印花税（卖出）  

Gate 内用该估计做 `k` 倍覆盖判断；真实成交仍走 broker/纸面撮合的完整成本。

## 6. 参数默认值

| 参数 | 默认 | 说明 |
|------|------|------|
| `max_stocks` | 3 | 最大持仓 |
| `n_enter` | 3 | 新开仓排名上限 |
| `n_exit` | 8 | 可持有区排名上限 |
| `m` | 3.0 | ATR 回撤带倍数 |
| `ma_period` | 20 | 趋势均线 |
| `trend_fail_days` | 2 | 连续失败日 → 强制卖 |
| `δ_σ` | 0.3 | 分数差 = δ_σ × σ(α) |
| `k` | 2.0 | 成本覆盖倍数 |
| `alpha_shrink` | 0.3 | alpha 加权收缩 |
| `optimizer` | `rank_vol` | 非 MVO |
| `swap_gate.enabled` | true | 总开关 |

配置建议挂在 `quant.yml`：

```yaml
portfolio:
  optimizer: rank_vol
  style_exposure:
    alpha_weighted: true
    alpha_shrink: 0.3
  swap_gate:
    enabled: true
    n_enter: 3
    n_exit: 8
    max_stocks: 3   # 或与 CLI/决策参数统一单一来源
    atr_band_mult: 3.0
    ma_period: 20
    trend_fail_days: 2
    delta_sigma: 0.3
    cost_cover_k: 2.0
```

（具体键名实现时可微调，但语义不变。）

## 7. 模块落点

| 位置 | 职责 |
|------|------|
| `quant/portfolio/swap_gate.py` | 打标、双门槛、目标代码集合、换出对 |
| `quant/portfolio/trend_state.py`（或并入 exit） | ATR 带 + MA20 + fail streak |
| `quant/portfolio/target.py` | 调用 SwapGate；alpha 加权 |
| `quant/exit/rules.py` + 纸面 `sell_watch` | 强制卖与 gate 对齐 |
| `quant/config/quant.yml` + `docs/CONFIG.md` / `OPERATIONS.md` | 配置与运维说明 |
| 单测 | 标签、拒绝追第一、双门槛、连续 2 日强制卖、ADV 成本使小票更难换 |

## 8. 验收回测

同窗口建议至少对照：

1. 日频 Top3 无 gate（追排名基线）  
2. 本设计 SwapGate（主方案）  
3. 旧参考：`aw_top10_10d`（日历冻结算）  

指标：总收益、年化、Sharpe、MDD、年化换手、成本占比、平均持仓天数、`force_exit` vs `swap` 归因。

样本：先用已有平静段；再补灾年/更长窗口外验后再改纸面默认。

## 9. 非目标（仍排除）

- 不以固定日历换仓为第一规则  
- 不以日频 MVO 为主路径  
- 不以「扫紧 hard_stop/ATR 参数救收益」替代 SwapGate  

## 10. 实现顺序

1. 趋势状态 + 标签单测  
2. SwapGate（分数差 + ADV 成本）单测  
3. 接入 `TargetPortfolio` + 引擎/决策  
4. exit / `sell_watch` 对齐  
5. 对照回测 → 调 δ_σ / k / m  
6. 文档与纸面默认参数落地  

## 11. 风险

- Top3 集中度高，单票波动与 MDD 可能大于 Top10  
- 趋势硬出场（N=2）若过敏，仍可能抬换手；需用回测看 `force_exit` 占比  
- ADV 估计偏差会导致门槛松紧失真；需与真实成交成本归因对照  
