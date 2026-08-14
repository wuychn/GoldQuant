# 入场动量过滤：对照实验 → 落地

> **For agentic workers:** 先跑对照实验拿证据，再按结果改默认配置；禁止未验证改参。

**Goal:** 用短灾区样本验证「关 hard_stop / 动量入场过滤 / 过滤+放宽止损」，再落地有效的入场过滤 A。

**Architecture:** `entry_filters` 过滤**新建仓**候选；`ExitConfig.hard_pct/atr_mult_stop=None` 可关硬止损；实验脚本一次建 alpha、多变体串行回测。

**Tech Stack:** Python、现有 `run_backtest` / `build_alpha_by_date`、`quant.yml`

## Global Constraints

- 接口级换源原则不变；本改动只动 portfolio/exit/backtest 调用链
- 过滤只挡新开仓，不因过滤强平已有仓
- 实验默认窗口：`2021-07-01`～`2022-06-30`（净值腰斩主灾区），`--home D:/ProgramData/.quant`

---

## Task 1: ExitConfig 支持关闭 hard_stop

- [x] `ExitConfig.hard_pct` / `atr_mult_stop` 改为 `float | None`
- [x] `evaluate_exits`：`hard_pct is None` 时不调用 `hard_stop`
- [x] 单测：None 时不触发 hard_stop

## Task 2: 入场动量过滤模块

- [x] 新增 `quant/portfolio/entry_filters.py`：`overheat_codes(daily, as_of, lookback, max_ret)`
- [x] `TargetPortfolio` 增加 `max_entry_ret_5d: float | None`，在 `target_weights` 对**非当前持仓**剔除
- [x] `from_config` 读 `portfolio.entry_filter.max_ret_5d`
- [x] 单测 + `quant.yml` / `CONFIG.md` 注释

## Task 3: 对照实验脚本并跑灾区样本

- [x] `scripts/research/exit_entry_ablation.py`：baseline / no_hard_stop / mom15 / mom15_wide
- [x] alpha 落盘缓存，变体只重跑 engine
- [x] 输出 metrics 对比表到 `reports/bt_ablation/`

## Task 4: 按结果落地 A

- [x] mom15 改善终值/MDD → 启用 `max_ret_5d: 0.15`
- [x] 关 hard_stop 无效；mom25 过松；记录于 summary.md
