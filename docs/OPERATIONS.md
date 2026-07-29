# 运维与操作指南

## 1. 环境要求

- Python **3.11+**（推荐 3.11）
- 可访问外网（拉取行情）
- Windows / Linux 均可

---

## 2. 安装

在项目根目录 `GoldQuant` 下：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

编辑 `.env`：至少配置 API 端口、飞书、LLM（见 [ENV.md](./ENV.md)）。

---

## 3. 启动数据 API

**必须在项目根目录执行**，且已激活 venv。**量化机器人依赖此服务拉数据，须先启动 API。**

```powershell
# 推荐（读取 .env 端口）
python -m app

# 或
uvicorn app.main:app --host 0.0.0.0 --port 8085
```

- Swagger：<http://127.0.0.1:8085/docs>
- 健康检查：<http://127.0.0.1:8085/health>

Linux 后台常驻：

```bash
chmod +x run.sh
./run.sh start
```

**Fixture 模式**：设 `GOLDQUANT_QUANT_USE_LOCAL_FIXTURE=true`，quant CLI 读 `data/fixtures/*.json`，不请求 API（联调/离线测试）。

---

## 4. 运行量化机器人

### 4.1 CLI 模式

```powershell
# 运维推送
python -m quant news
python -m quant pre_market
python -m quant during_market          # 盘中先卖后买 + 推送
python -m quant post_market_lunch
python -m quant post_market_evening

# 日决策（T 晚选股 + 作战池/卖出监控 + 推送）
python -m quant daily_decision
python -m scripts.decision.daily --no-push    # 仅落盘
python -m scripts.decision.daily --dry-run     # 不撮合

# 预取概念/基本信息（可选）
python -m quant prefetch_concepts
```

加 `--no-push` 只落盘不推飞书。

### 4.2 建议调度（quant.yml → scheduler）

| 时间 | 模式 | 说明 |
|---|---|---|
| 05:00 | `prefetch_concepts` | 预取持仓/自选概念与 jbxx |
| 8–22 每个整点 | `news` | LLM 新闻摘要 |
| 09:25 | `pre_market` | 盘前推送 |
| 09:37–15:00 每 7 分钟 | `during_market` | 盘中择时买卖 |
| 11:50 | `post_market_lunch` | 午间复盘 |
| 16:00 | `maintain` | 离线库维护 |
| 20:10 | `daily_decision` | T 晚选股 |

启动 API 时若 `scheduler.enabled: true`，`app/scheduling/quant_scheduler.py` 会自动注册上述任务。也可 cron / 任务计划手动调用 `python -m quant <mode>`。

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
python -m scripts.data.build_daily --start 2021-01-01 --workers 3
```

- 拉全 A 历史（`stock_zh_a_hist` 不复权）+ 指数 + 日历 + 退市股
- 支持断点续传（跳过已落库代码）
- 建议 `--workers 3`，过高易被东财限流
- 数千只全量历史可能耗时数小时

### 5.3 日增量

```powershell
python -m scripts.data.update_daily
python -m scripts.data.update_daily --date 2026-07-25
```

收盘后执行：`spot_em` → 追加当日 daily_raw、除权检测、复权刷新、universe/行业/listing、fundamental_pit、因子快照（flow/hot/theme）。

**注意**：spot_em 无历史，当日没抓就补不回来，失败须告警。

### 5.4 自动维护（推荐）

```powershell
python -m scripts.data.maintain
python -m scripts.data.maintain --date 2026-07-25
```

逻辑（`scripts/data/maintain.py`）：

1. 库为空 → 全量 `build_daily`
2. 有库 → 扫描交易日历缺口 → `build_daily --ignore-existing` 回补
3. 最后跑 `update_daily` 当日增量

默认由调度器 **16:00** 触发。

### 5.5 其他数据脚本

| 命令 | 作用 |
|---|---|
| `python -m scripts.data.build_fundamental_pit` | 批量建财务 PIT |
| `python -m scripts.data.build_listing_dates` | 上市日表 |
| `python -m scripts.data.backfill_factor_snapshots` | 因子快照回填 |
| `python -m scripts.data.verify_daily` | 数据校验 |
| `python -m scripts.data.audit_data_health` | 健康审计 |

### 5.6 复权说明

- 因子/回测/出场：**必须**用 `load_adjusted_daily()`（后复权）
- 撮合/涨跌停：用 daily_raw 真实价
- 除权检测：`detect_ex_dividend_codes` → `refresh_adj_for_codes`

---

## 6. 回测

### 6.1 主回测

```powershell
python -m scripts.backtest.run --start 2024-01-01 --end 2024-06-30
```

常用参数：

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

| 命令 | 作用 |
|---|---|
| `python -m scripts.backtest.validate` | 随机 alpha 基准 + 前视泄漏检验 |
| `python -m scripts.backtest.run_intraday_timing` | 盘中择时 θ 增益验证（日频代理） |

### 6.3 回测前置条件

- 离线库覆盖回测区间（至少 daily_raw + adj）
- ML/IC 校准建议 ≥100 样本；结论宜覆盖趋势+震荡各一轮
- 回测路径（差额 rebalance）与实盘（作战池+盘中择时）**不完全一致**，Sharpe 作参考而非实盘预期

---

## 7. 因子研究脚本

| 命令 | 作用 |
|---|---|
| `python -m scripts.factors.build_panel` | 构建因子面板 |
| `python -m scripts.factors.ic_report` | IC/ICIR 报告 → `$QUANT_HOME/reports/ic/` |
| `python -m scripts.factors.fit_weights` | 拟合 factor_weights.yml |
| `python -m scripts.research.walk_forward` | walk-forward → factor_weights_ts.yml |
| `python -m scripts.research.delist_bias_audit` | 退市偏差审计 |
| `python -m scripts.research.calibrate_slippage` | 滑点校准 |

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
python -m scripts.decision.daily --dry-run
python -m scripts.smoke_e2e          # 端到端冒烟（若有）
```

---

## 10. 常见问题

**Q：quant 报连接失败？**  
A：确认 `python -m app` 已启动，端口与 `quant/config.py` 中 `BASE_URL` 一致（默认 `http://localhost:8085`）。或开 fixture 模式。

**Q：面板为空 / 决策失败？**  
A：检查离线库是否已 build；至少需覆盖决策日前若干交易日。

**Q：`.env` 端口不生效？**  
A：使用 `python -m app` 启动；裸 `uvicorn` 需显式 `--port`。

**Q：回测与实盘差异？**  
A：回测用 strict 差额 rebalance；实盘 T+1 盘中 intraday_alpha 触发。见 [ARCHITECTURE.md §4.5](./ARCHITECTURE.md#45-回测-vs-实盘纸面)。
