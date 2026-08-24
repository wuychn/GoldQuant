# 波段状态机策略设计

日期：2026-08-21  
更新：2026-08-22（同窗扫参 → 反转 IC 日历冻结）  
状态：研究验收通过量级目标（总收益>50%）；纸面未接  
范围：独立波段买卖规则（回测优先；确认后再接纸面）

## 1. 背景

用户目标（波段）：波段信号买卖。价量状态机与 SwapGate 路径分离。

## 2. 2026-08-22 同窗结论（硬结果）

窗口：`2023-08-01`～`2025-12-31`（588 交易日），`strict_signals=True`。

| 路径 | 代表配置 | 总收益 | 备注 |
|------|----------|--------|------|
| 价量 pullback/momentum + ATR 卖 | v3_base 等 | **-23%～-92%** | 不达标 |
| 动量日历冻结 top10/10d | cal_top10_10d | **-96%** | A 股短线动量失效 |
| IC alpha 日历冻结（不取负） | aw_top10_10d | **-96%** | 符号反了 |
| **IC alpha 取负 + 日历冻结** | **inv_t10_e20** | **+68.6%** | **达标** |
| 同上 | inv_t12_e15 | +56.5% | 备选 |
| 同上 | inv_t12_e20 | +53.1% | 备选 |

推荐默认：`invert=true, topn=10, rebalance_every=20`，`exit_config=None`。  
冲「单月≥10%出现率」：同窗扫参最优为 `topn=3, every=20`（28 个月中 10 个月≥10%，总收益约 +170%；**不是**每月都 ≥10%，均值月约 +3.8%）。见 `scripts/research/swing_monthly10_explore.py`。

脚本：`scripts/research/swing_alpha_calendar.py`  
yml：`swing_band.alpha_calendar`

风险：参数在同窗网格选出，存在过拟合；须看 holdout / 外推窗再接纸面。

## 3. 价量状态机草案（保留备查，本窗未达标）

见历史版本与 `quant/swing/signals.py` / `policy.py`。买入 ATR 动量+位置分位；卖出转跌/滞涨/时间 OR。

## 4. 模块落点

| 位置 | 职责 |
|------|------|
| `quant/swing/signals.py` | 价量买/卖纯函数 |
| `quant/swing/policy.py` | `SwingBandPolicy` |
| `quant/swing/calendar_policy.py` | 价量日历冻结 |
| `scripts/research/swing_alpha_calendar.py` | **主验收**：IC alpha ±取负 + 日历冻结 |
| `quant/config/quant.yml` → `swing_band.alpha_calendar` | 推荐参数 |

## 5. 验收

窗口：`2023-08-01`～`2025-12-31`。  
研究成功标准：总收益 > 50% — 已由 `inv_t10_e20` 达到。合并纸面前须补分段/外推窗，并统一 alpha 取负口径。
