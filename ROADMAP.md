# GoldQuant 路线图

> 状态基线（2026-07-31）：r3 重构完成（r1 评分/战法/ML 体系退役，统一 factors IC 链）、数据源四协议抽象完成、全量 181 测试绿、回测保真两处硬伤已修（除权尖刺 + 基准缺失）、文档体系已整理。
>
> 本路线图按"先可信、再扩充、后工程化"排序，**未排期的远期项保留原意向、不承诺时间**。

---

## Phase 0 · 可信基线化（进行中）

> 目标：让回测数字**第一次值得相信**。不改范式，只把数据地基本 + 验证闭环跑通。

- [ ] 全量离线库：`build_daily --start 2021-01-01 --workers 4 --req-interval 2,4`（沪深300 起跑通，再扩全 A）
- [ ] `audit_data_health` 验后复权价连续性（无 >28% 单日跳空）+ 复权/name/快照覆盖
- [ ] `fit_weights` walk-forward OOS → `factor_weights_ts.yml`（看 t>2 且过 BH-FDR 的因子数）
- [ ] `backtest.run --start 2021-01-01 --end 2026-07-31 --sensitivity` → 首组可信 Sharpe/回撤/换手/超额/容量
- [ ] `update_daily` 收盘后日常化（PIT name/industry/universe 快照逐日累积）

**已知限制**：历史 PIT name 快照不可回填（`spot_em` 实时无历史），历史段 ST 过滤与行业中性化退化；从今日起随 `update_daily` 逐日改善。

---

## Phase 1 · 因子扩充（近期）

> 详见 [docs/FACTORS.md §5](docs/FACTORS.md#5-待补因子与顶级量化的差距)。按"IC 增量 × A 股可得性"排序，**先估 IC 增量再纳入**，不达标自动零权重。

- [ ] **基本面深度**：应计项目 / CFO·NI / NOA / ΔROE / 毛利率 / EV·EBITDA（数据现成、共线低，首选）
- [ ] **事件因子**：PEAD + SUE（`fundamental_pit` 已有 announce_date）/ 限售解禁 / 龙虎榜机构席位
- [ ] **风险因子显式化**：size 当信号（A 股小盘效应）/ IVOL / 下行 β / 协偏度
- [ ] **北向 + 资金流多档**：扩 flow 族（`stock_hsgt_*` + 超大/大/中/小单分布）
- [ ] **挤共线**：`mom_60`/`mom_120_20`、`ma_spread`/`spread_accel_5` 去冗余
- [ ] 暂缓：分析师预期 / 社交情绪（数据质量差）

---

## Phase 2 · 策略健壮（中近期）

> 目标：回测保真与执行层风控的残留硬伤收口（详见 commit `9847783`..`146f79a` 的复核记录）。

- [ ] **回测保真 C5**：partial fill rollover / 封板判定统一 / microstructure 滑点 `day_change_pct` 前视 / strict_signals 引擎切 T-1 rows
- [ ] **回测保真 C6**：regime 仓位重评估（r3 决策链已不用 regime 仓位，转 target_vol；需重评）；IC 标签 close→open 口径
- [ ] **MVO SLSQP 失败回退补分组约束投影**（`portfolio/mvo.py`：解析回退现仅 `apply_single_cap`，忽略 sector/concept/style cap）
- [ ] **双链路统一**：涨跌停 `limit_state↔at_limit_up_down` 合一 + parity + θ 进配置 + partial_fill 接线
- [ ] **voltarget 前视复核** / `TargetPortfolio` 总权重不归一化（设计取舍，确认保持）

---

## Phase 3 · 机器学习（中期，需 OOS 严格才动）

> r3 已退役 r1 的 `ml/` 体系（calibrate/optimizers/dataset 全删）。此处指**在因子框架内**做非线性增强，非另起 ML 栈。

- [ ] 非线性合成：GBDT/LightGBM on factor z-score → 预测截面收益（替代 `compose_alpha` 的线性加权），**必须 walk-forward + OOS + 多重检验**
- [ ] 因子选择自动化：基于 OOS ICIR 的滚动筛选（而非全样本 BH-FDR 一次性）
- [ ] 协方差估计升级：shrinkage / DCC-GARCH 替代 EWMA
- [ ] 横截面中性化扩展：风格因子（size/value/mom/低波）中性

> 前置：Phase 0 基线可信 + Phase 1 因子扩充到位——否则在不可信数字上调 ML 等于过拟合噪声。

---

## Phase 4 · 多数据源与商业数据（中长期）

- [ ] **tushare 接入**：实现 `DailySource`/`MarketSource` 协议 + 注册（换源只改配置，3 步不动业务，见 [ARCHITECTURE §5.1.1](docs/ARCHITECTURE.md#511-怎么替换数据源)）
- [ ] **商业数据**：Wind / 聚源 / 米筐（财务深度字段、分析师预期、Level-2）——补 Phase 1 标⚠️/❌ 的因子
- [ ] 离线库构建脚本去 akshare 硬编码（`adjust`/`calendar`/`delist`/`fundamental_pit` 现直连 akshare，属"造库层"非四协议范畴，统一切源需单独立项）

---

## Phase 5 · 页面（远期，未排期）

- [ ] 决策可视化：日决策卡、作战池、卖出监控、纸面权益曲线的 Web UI
- [ ] 回测/IC 交互式报告（现 `$QUANT_HOME/reports/` 为静态文件）
- [ ] 因子监控面板（OOS IC 衰减、权重漂移、容量告警）

---

## Phase 6 · 对接真实券商（远期，未排期）

> 前置：Phase 0 基线可信 + Phase 2 策略健壮——在不可信数字上接实盘是负债。

- [ ] 券商 SDK 接入（QMT / 掘金 / 恒生 PTrade 等 A 股个人可用的程序化接口）
- [ ] 纸面→实盘对账：纸面 `paper_account` 与实盘持仓/权益一致性校验
- [ ] 实盘风控闸门：纸面 `risk_gate` 的同口径落地（日内回撤/熔断/冷却/卖后禁买回）
- [ ] 失败兜底与幂等（订单回报对账、断线重连、重复下单防护）

---

## 不在路线图内（明确边界）

- 分钟级打板 / T0 / 高频——本系统定位日频波段，不适配（见 [ARCHITECTURE §1](docs/ARCHITECTURE.md#1-系统定位)）
- 投资建议——本仓库仅数据聚合与纸面模拟辅助，**不构成投资建议**
