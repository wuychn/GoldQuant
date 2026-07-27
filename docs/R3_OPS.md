# r3 日运维与数据约定

> 架构、因子/组合/出场指标与交易闭环见 **[R3_ARCHITECTURE.md](./R3_ARCHITECTURE.md)**。

## 报告落盘

全部默认写到 **`$QUANT_HOME/reports/`**（不再写仓库根目录 `reports/`）：

| 子目录 | 内容 |
|--------|------|
| `reports/decision/` | 日决策卡 / 纸面成交 |
| `reports/ops/` | 新闻/盘前/盯盘/复盘推送正文 |
| `reports/bt/` | 回测 |
| `reports/wf/` | walk-forward |
| `reports/ic/` | IC 报告 |

## 纸面持仓字段

- **不再写入「战法」**（专家系统遗留）
- **买入原因**格式：`side|rank=N|α=x.xxx|tw=x.x%|Δw=±x.x%|业务原因`

## CLI

```bash
python -m quant news
python -m quant pre_market
python -m quant during_market
python -m quant post_market_lunch
python -m quant post_market_evening
python -m quant daily_decision          # 决策+纸面+推送
python -m scripts.decision.daily        # 同上（脚本入口）
```

加 `--no-push` 可只落盘不推飞书。

## 飞书事件（r3）

| 标签 | 模式 | 要点 |
|------|------|------|
| 新闻聚焦 | news | LLM 去噪要点 + 综合解读 |
| 盘前准备 | pre_market | 新闻要点 / 指数 / 纸面账户 / 关注 |
| 智能盯盘 | during_market | 指数 / 纸面持仓 / 异动 |
| 午间复盘 | post_market_lunch | 午前指数 + 纸面账户 |
| 收盘复盘 | post_market_evening | 收盘指数 + 纸面绩效/持仓 |
| 日决策·纸面成交 | daily_decision | 指令 / 成交 / 账户 / 持仓 |

格式：纯文本，`标题\n时间\n\n【小节】\n行…`，简洁可读。
