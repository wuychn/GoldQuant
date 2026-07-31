# 因子说明

因子在**后复权**序列上计算，仅用 `≤ as_of` 数据。流水线：

```text
raw × direction → winsorize(1%, 99%) → 行业 + log(流通市值) 中性 → 截面 z-score
  → Σ w_i · z_i = alpha（日频）
  → 或 compose_intraday_alpha（盘中，池内 z-score）
```

- **中性化**：`quant/factors/neutralize.py`
- **日频合成**：`quant/factors/compose.py:compose_alpha`
- **盘中合成**：`quant/factors/compose.py:compose_intraday_alpha`
- **注册表**：`quant/factors/registry.py`（`REGISTRY`，20 个日频因子）

**direction**：+1 表示原始值越大越看多；-1 表示取反后再中性化。

**权重**：live 优先 `factor_weights_ts.yml`（walk-forward），否则 `factor_weights.yml`，再否则 registry 默认。

---

## 1. 日频因子（20 个）

合成公式：

\[
\alpha(code) = \frac{\sum_i w_i \cdot z_i}{\sum_i w_i}
\]

\(z_i\) 为中性化后截面 z-score；缺失因子按 0 贡献。

### 1.1 动量族（`library/momentum.py`）

| 因子 | 含义 | 计算 | 方向 | 默认权重 | 数据来源 |
|---|---|---|---|---|---|
| `mom_20` | 短期收益（A 股偏反转） | \(C_t/C_{t-20}-1\) | **-1** | 0.8 | 后复权 close |
| `mom_60` | 中期动量 | \(C_t/C_{t-60}-1\) | +1 | 1.0 | 后复权 close |
| `mom_120_20` | 跳过近 20 的中期动量 | \(C_{t-20}/C_{t-120}-1\) | +1 | 1.0 | 后复权 close |

### 1.2 质量族（`library/quality.py`）

| 因子 | 含义 | 计算 | 方向 | 默认权重 | 数据来源 |
|---|---|---|---|---|---|
| `eff_ratio_60` | Kaufman 效率比 | \|净涨幅\| / Σ\|日收益\|（60 日） | +1 | 1.0 | 后复权 close |
| `flip_rate_60` | 方向反转频率 | 60 日涨跌符号翻转比例 | **-1** | 0.6 | 后复权 close |
| `vol_60` | 年化波动 | std(日收益,60) × √252 | **-1** | 0.8 | 后复权 close |

### 1.3 基本面族（`library/fundamental.py`）

| 因子 | 含义 | 计算 | 方向 | 默认权重 | 数据来源 |
|---|---|---|---|---|---|
| `ep_ttm` | 盈利收益率 | `extras['ep_ttm']` 或 1/PE_TTM | +1 | 0.8 | `fundamental_pit` PIT 表 |
| `bp` | 账面市值比 | `extras['bp']` 或 1/PB | +1 | 0.6 | 同上 |
| `roe` | 净资产收益率 | `extras['roe']` | +1 | 1.0 | 同上 |
| `rev_yoy` | 营收同比 | `extras['rev_yoy']` | +1 | 0.8 | 同上 |

PIT 注入：`panel_builder` 经 `metrics_as_of(as_of)` 写入 `bars.extras`。存储：`~/.quant/data/store/fundamental_pit/`。

### 1.4 位置/趋势族（`library/position.py`）

| 因子 | 含义 | 计算 | 方向 | 默认权重 | 数据来源 |
|---|---|---|---|---|---|
| `dist_high_252` | 距 52 周高 | \(C/\max(H_{252})-1\) | +1 | 1.0 | 后复权 high/close |
| `ma_spread` | 均线发散 | MA5/MA20 - 1 | +1 | 0.8 | 后复权 close |
| `spread_accel_5` | 发散加速度 | ma_spread(t) - ma_spread(t-5) | +1 | 0.6 | 后复权 close |
| `ma_slope_20` | MA20 斜率 | (MA20_now - MA20_prev) / price | +1 | 0.6 | 后复权 close |

### 1.5 量能族（`library/volume.py`）

| 因子 | 含义 | 计算 | 方向 | 默认权重 | 数据来源 |
|---|---|---|---|---|---|
| `vol_ratio_5_20` | 放量 | mean(amount,5) / mean(amount,20) | +1 | 0.8 | 后复权 amount |
| `turnover_z_60` | 换手异常 | (TO - μ_60) / σ_60 | +1 | 0.6 | 后复权 turnover_rate |
| `vol_price_corr_20` | 量价配合 | corr(日收益, volume, 20) | +1 | 0.6 | close + volume |

### 1.6 资金族（`library/flow.py`）

| 因子 | 含义 | 计算 | 方向 | 默认权重 | 数据来源（优先级） |
|---|---|---|---|---|---|
| `flow_ratio_5` | 5 日主力净流入强度 | 5 日主力净流入 / 流通市值 | +1 | 0.6 | ① PIT 快照 `store/fund_flow/` ② 量价代理：(sum(amount[-5:])-sum(amount[-10:-5]))/float_mv ③ 实时：`ak.stock_zh_a_spot_em` 或东财 zj |

回测中常缺快照，会回退量价代理。

### 1.7 主题族（`library/theme.py`）

| 因子 | 含义 | 计算 | 方向 | 默认权重 | 数据来源 |
|---|---|---|---|---|---|
| `theme_mom` | 主题强度 | `extras['theme_mom']`（0–1 分位） | +1 | 0.6 | ① PIT 快照 `store/theme_mom/`（东财概念板块涨跌幅 + 个股概念映射） ② 回退：同行业 mom_20 截面分位 |

采集：`factor_capture.capture_theme_mom`（收盘后批量）。

### 1.8 热度族（`library/hot.py`）

| 因子 | 含义 | 计算 | 方向 | 默认权重 | 数据来源 |
|---|---|---|---|---|---|
| `hot_rank_z` | 人气 | 东财人气榜排名截面 z（取负，排名越前 z 越大） | +1 | 0.4 | PIT 快照 `store/hot_rank/`；采集：`ak.stock_hot_rank_em()` |

回测中常空（无历史快照）。

---

## 2. 盘中因子（择时层，5 个）

**不进日频 alpha**。输入：`SpotRow`（`fetch_spot_em` 实时快照）。

| 因子 | 含义 | 计算（live） | 方向 | 默认权重 | spot 字段 |
|---|---|---|---|---|---|
| `intraday_strength` | 盘中走强 | (最新价 - 今开) / 今开 | +1 | 1.0 | close, open |
| `volume_ratio` | 量比 | vol_ratio | +1 | 1.0 | vol_ratio |
| `speed` | 涨速 | speed(%) | +1 | 0.8 | speed |
| `day_change` | 当日涨跌 | pct(%) | +1 | 0.6 | pct |
| `turnover` | 换手率 | turnover_rate(%) | +1 | 0.5 | turnover_rate |

**合成**：作战池内截面 z-score 加权，**不做**行业/市值中性化。

**触发**：α_z ≥ **θ = 1.0**（`intraday.py:run_intraday_session`）。

**日频代理回测**（`spot_row_from_daily`）：用日 K 构造 SpotRow；`speed` 用 \((close-open)/open×100\) 近似。

代码：`quant/factors/library/intraday.py`。

---

## 3. 权重与研究

### 3.1 默认权重来源

```text
load_factor_weights_info(as_of)
  1. ~/.quant/config/factor_weights_ts.yml  （walk-forward，strict OOS 优先）
  2. ~/.quant/config/factor_weights.yml     （静态 IC 拟合）
  3. REGISTRY.weights()                     （代码内 default_weight）
```

### 3.2 IC 拟合流程

| 脚本 | 作用 | 产物 |
|---|---|---|
| `scripts/factors/build_panel.py` | 构建历史因子面板 → `data/panel.parquet` | 面板 |
| `scripts/factors/ic_report.py` | 因子 IC/ICIR 报告（含 IC 衰减） | `$QUANT_HOME/reports/ic/` |
| `scripts/factors/fit_weights.py` | walk-forward OOS 权重（默认）；`--static` 写静态全样本权重 | `factor_weights_ts.yml` / `factor_weights.yml` |
| `scripts/research/walk_forward.py` | 时变权重（另一入口） | `factor_weights_ts.yml` |

**ICIR 权重**（`quant/factors/weights.py`）：权重 ∝ ICIR；过滤 `ic_mean`、`icir`、`t_stat` 阈值；FDR 由 `fit_weights.py --fdr-alpha` 控制（BH-FDR，单侧 IC>0 检验）。

### 3.3 研究参数（quant.yml）

| 键 | 默认 | 含义 |
|---|---|---|
| `factors.strict_oos_weights` | true | live 禁用全样本静态 IC 权重 |

IC 持有期、FDR、晋升门槛等由研究脚本的 CLI 参数控制，不在 yml 中配置。

---

## 4. 面板构建细节

`quant/factors/panel_builder.py:build_panel`：

1. 从 `load_adjusted_daily` 构造 `BarSeries`（后复权 OHLCV + float_mv）
2. 注入 PIT：`fundamental_pit`、`fund_flow`、`hot_rank`、`theme_mom` 快照
3. 各因子算 raw → winsorize → 行业+市值中性 → z-score
4. 可选计算 forward return（研究用）

**关键约束**：因子、ATR、MA 必须走后复权，否则除权日假跳空污染 alpha/出场。
