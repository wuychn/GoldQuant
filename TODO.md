# TODO · 下一步

> 当前状态：代码侧无阻塞（hfq 除权尖刺 + 基准缺失已修、181 测试全绿、文档闭环）。
> 唯一卡可信回测的是数据不够。下面是 **Phase 0 可信基线化**的执行清单，按序勾。
> 完整路线图见 [ROADMAP.md](ROADMAP.md)；命令一律用 `poetry run python -m ...`（见 [docs/OPERATIONS.md](docs/OPERATIONS.md)）。

## 1. 全量拉离线库 + 每日增量合并（build_daily 到昨日 + update_daily 今日起）

**方案**：全量 build 需数小时/数天，期间不想与日常 update_daily 抢同一 store——分 home 跑，build 完成后合并。

- [ ] 离线 build 到昨日（home 分开）：
      `QUANT_HOME=~/.quant/offline poetry run python -m scripts.data.build_daily --start 2021-01-01 --end <昨日> --workers 1 --req-interval 5,10`
- [ ] 每日盘后增量（另一 home）：
      `QUANT_HOME=~/.quant/daily poetry run python -m scripts.data.update_daily`
- [ ] build 完成后合并到统一 home：
      `poetry run python -m scripts.data.merge_library --offline ~/.quant/offline --daily ~/.quant/daily --out ~/.quant`
      （daily_raw 归一 13 列、update 覆盖 build、复权沿用 raw+factor 分离不复发除权尖刺）
- [ ] 补历史段缺列（float_mv/total_mv 精确市值 + pre_close；默认只拉缺市值码，可重跑；`--force` 全量）：
      `poetry run python -m scripts.data.backfill_daily_meta --home ~/.quant`

> 起始日 2021-01-01 给 ~5 年历史，够 walk-forward 训练窗 504 日 + OOS。
> curl_cffi 已绕东财 TLS；`build_daily` 支持智能断点续传，中断后重跑同一命令即按完整性续补。
> 合并/校验详见 [docs/OPERATIONS.md §5.6a/5.6b](docs/OPERATIONS.md#56a-离线库与每日增量合并merge_library)。

## 2. 校验数据

- [ ] `poetry run python -m scripts.data.validate_library --home ~/.quant`
      一键验：adj_factor 覆盖（<90% FAIL）、index 000300·000905·000852、市值 float_mv/pre_close
      覆盖、重复/价格 sanity/**后复权单日跳空 >28%**（复权 bug 探测器）。退出码 0=通过、1=发现问题。
- [ ] 不通过先修数据（补复权因子/缺口）再往下。

## 3. walk-forward IC 权重（OOS，勿用 --static）

- [ ] `poetry run python -m scripts.factors.fit_weights --start 2021-01-01 --end 2026-07-31 --min-tstat 2.0 --fdr-alpha 0.05`
      产物 `~/.quant/config/factor_weights_ts.yml`；看 t>2 且过 BH-FDR 的因子数。
- [ ] 0 因子入选 → 说明 universe/窗口还不够，或因子在 OOS 无显著性（非管道问题）。

## 4. 首组可信回测

- [ ] `poetry run python -m scripts.backtest.run --start 2021-01-01 --end 2026-07-31 --sensitivity`
      看：Sharpe / 最大回撤 / 年化换手 / 超额（基准 000300 已接入）/ 容量 / 分年一致性 / OOS 衰减。
- [ ] 严格口径：T-1 收盘因子 → T 开盘成交（默认 strict，勿加 `--loose`）。
- [ ] Sharpe 仅作参考——回测路径（差额 rebalance）与实盘（作战池+盘中择时）不完全一致。

## 5. 日常化

- [ ] 收盘后跑 `poetry run python -m scripts.data.update_daily`，PIT name/industry/universe 快照逐日累积。
- [ ] 已知限制：历史 PIT name 不可回填（spot_em 实时无历史），历史段 ST 过滤/行业中性化退化，从今日起逐日改善。

---

## 完成后进入

- [ ] Phase 1 因子扩充（基本面深度 / 事件 PEAD·SUE / 风险因子显式化），详见 [docs/FACTORS.md §5](docs/FACTORS.md#5-待补因子与顶级量化的差距)。
