# FastAPI 数据服务 API

> 前缀：**`/api/v1`**（`GOLDQUANT_API_V1_STR`）  
> 完整交互文档：<http://127.0.0.1:8085/docs>（`poetry run python -m app` 启动后访问）

## 1. 架构

```text
app/main.py
  └── api/v1/router.py
        ├── quant_endpoint.py      # 量化决策 payload（五时段）
        ├── mkt_heat.py            # 热度/人气
        ├── mkt_quotes.py          # 行情/K线
        ├── mkt_funds.py           # 资金面
        ├── mkt_board.py           # 板块/概念/行业
        ├── mkt_extremes.py        # 异动/涨跌停/股池
        ├── mkt_sentiment.py       # 市场情绪
        ├── mkt_screens.py         # 排行/形态
        ├── mkt_briefs.py          # 资讯快讯
        ├── mkt_dealer.py          # 龙虎榜
        ├── mkt_corporate.py       # 公司/新股
        ├── mkt_research.py        # 机构/评级
        ├── mkt_interconnect.py    # 沪深港通
        ├── mkt_disclosure.py      # 停复牌/业绩
        ├── mkt_block.py           # 大宗交易
        ├── mkt_margin.py          # 融资融券
        └── mkt_config.py          # 东财 header 管理
```

启动时 `lifespan` 会：注入代理、启动 APScheduler（读 `quant.yml` scheduler 段）。

---

## 2. 5 板推送

不走 HTTP。盘后 `scripts.tactic.close`，盘中 `scripts.tactic.intraday`，经飞书发出。

---

## 3. 行情与热度（常用）

| 说明 | 路径示例 |
|---|---|
| 东财人气榜 | `/api/v1/hot/eastmoney/popularity` |
| 同花顺热榜 | `/api/v1/hot/ths` |
| 东财个股资讯 | `/api/v1/news/em?symbol=` |
| 东财实时行情 | `/api/v1/stock/em/...`（见 mkt_quotes） |
| 板块/概念 | `/api/v1/board/...`（见 mkt_board） |

具体路径以 Swagger `/docs` 为准。

---

## 4. 与调度的关系

`poetry run python -m app` 启动 HTTP 和调度：16:00 日更、20:10 复盘、周五维护。盘中指令由 Windows 任务 `GoldQuantTacticIntraday` 推送。单次复盘用 `python -m quant daily_decision`，不必先启 API。

---

## 5. 健康检查

```bash
curl http://127.0.0.1:8085/health
```

---

## 6. CORS 与代理

- CORS：`GOLDQUANT_CORS_ORIGINS`、`GOLDQUANT_CORS_ALLOW_CREDENTIALS`
- 出站代理：`GOLDQUANT_PROXY_*`（AKShare、同花顺、东财等统一走应用代理配置）

详见 [CONFIG.md](./CONFIG.md)。
